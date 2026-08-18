using System.Reflection;
using System.Text.Json;
using NLog;
using NzbDrone.Core.CustomFormats;
using NzbDrone.Core.Download.Aggregation.Aggregators;
using NzbDrone.Core.Languages;
using NzbDrone.Core.MediaFiles;
using NzbDrone.Core.Movies;
using NzbDrone.Core.Parser;
using NzbDrone.Core.Parser.Model;

try
{
    if (args.Length < 3)
    {
        Console.Error.WriteLine(
            "usage: RadarrWorker <custom-format-directory> <quality-profile-directory> <case-file> [case-file ...]");
        return 2;
    }

    var formats = FormatLoader.Load(args[0]);
    var trashIdByFormatId = formats.ToDictionary(x => x.Format.Id, x => x.TrashId);
    var formatByTrashId = formats.ToDictionary(x => x.TrashId);
    var formatNames = formats.ToDictionary(x => x.TrashId, x => x.Format.Name);
    var profiles = GuideProfileCatalog.Load(args[1], formatByTrashId);
    var cases = TestContract.LoadCases(args.Skip(2), "radarr");
    var service = new CustomFormatCalculationService(new MemoryFormatService(formats.Select(x => x.Format).ToList()), LogManager.GetCurrentClassLogger());
    var failed = false;

    foreach (var test in cases)
    {
        TestContract.ValidateReferences(test, formatNames, "Radarr");

        var input = test.Input;
        var inputType = input.Type;
        var parseTitle = inputType switch
        {
            "localFile" => input.SceneName ?? Path.GetFileName(input.Path),
            "storedFile" => input.SceneName ?? Path.GetFileName(input.OriginalFilePath) ?? Path.GetFileName(input.RelativePath),
            _ => input.Title
        };
        var parsed = NzbDrone.Core.Parser.Parser.ParseMovieTitle(parseTitle)
            ?? throw new InvalidOperationException($"Radarr could not parse '{parseTitle}'");
        var suppliedLanguages = input.Languages == null
            ? parsed.Languages
            : ResolveLanguages(input.Languages);
        var indexerFlags = ResolveIndexerFlags(input.IndexerFlags);
        var remoteMovie = inputType == "remoteRelease"
            ? CreateRemoteMovie(input, parsed, suppliedLanguages, indexerFlags)
            : null;
        var languages = remoteMovie?.Languages ?? suppliedLanguages;
        var matchedFormats = inputType switch
        {
            "localFile" => service.ParseCustomFormat(new LocalMovie
            {
                Path = input.Path,
                Size = input.SizeBytes,
                Movie = new Movie { Title = input.MediaTitle, Year = input.Year },
                Quality = parsed.Quality,
                Languages = languages,
                IndexerFlags = indexerFlags,
                ReleaseGroup = parsed.ReleaseGroup,
                Edition = parsed.Edition,
                SceneName = input.SceneName
            }),
            "storedFile" => service.ParseCustomFormat(new MovieFile
            {
                RelativePath = input.RelativePath,
                OriginalFilePath = input.OriginalFilePath,
                SceneName = input.SceneName,
                Size = input.SizeBytes,
                Quality = parsed.Quality,
                Languages = languages,
                IndexerFlags = indexerFlags,
                ReleaseGroup = parsed.ReleaseGroup,
                Edition = parsed.Edition
            }, new Movie { Title = input.MediaTitle, Year = input.Year }),
            "remoteRelease" => service.ParseCustomFormat(remoteMovie, input.SizeBytes),
            _ => throw new InvalidOperationException($"Unknown input type '{inputType}'")
        };
        var matched = matchedFormats
            .Select(x => trashIdByFormatId[x.Id])
            .OrderBy(x => x)
            .ToArray();
        var missing = test.Expected.Matches.Select(x => x.TrashId).Except(matched).OrderBy(x => x).ToArray();
        var unexpected = test.Expected.DoesNotMatch.Select(x => x.TrashId).Intersect(matched).OrderBy(x => x).ToArray();
        var profileScore = profiles.Evaluate(test, matchedFormats);
        var passed = missing.Length == 0 && unexpected.Length == 0 && (profileScore?.Passed ?? true);
        failed |= !passed;
        var diagnostics = passed
            ? Array.Empty<object>()
            : BuildDiagnostics(
                formatByTrashId,
                CreateDiagnosticInput(input, parsed, languages, indexerFlags),
                missing,
                unexpected);

        Console.WriteLine(JsonSerializer.Serialize(new
        {
            test.Name,
            test.Source,
            inputType,
            passed,
            parsed = new
            {
                parsed.ReleaseTitle,
                parsed.SimpleReleaseTitle,
                parsed.ReleaseGroup,
                quality = parsed.Quality?.ToString(),
                parsedLanguages = parsed.Languages.Select(x => x.Name),
                effectiveLanguages = languages.Select(x => x.Name)
            },
            matched,
            missing,
            unexpected,
            profileScore,
            diagnostics
        }, Json.Output));
    }

    return failed ? 1 : 0;
}
catch (Exception error)
{
    Console.Error.WriteLine($"ERROR: {error.Message}");
    return 2;
}

static List<Language> ResolveLanguages(IEnumerable<string> names)
{
    var known = Language.All.ToDictionary(x => x.Name, StringComparer.Ordinal);
    return names.Select(name => known.TryGetValue(name, out var language)
            ? language
            : throw new InvalidOperationException(
                $"Unknown Radarr language '{name}'. Valid names: {string.Join(", ", known.Keys.OrderBy(x => x))}"))
        .ToList();
}

static IndexerFlags ResolveIndexerFlags(IEnumerable<string> names)
{
    var known = Enum.GetNames<IndexerFlags>().ToHashSet(StringComparer.Ordinal);
    return (names ?? Array.Empty<string>()).Aggregate((IndexerFlags)0, (flags, name) =>
        known.Contains(name)
            ? flags | Enum.Parse<IndexerFlags>(name)
            : throw new InvalidOperationException(
                $"Unknown Radarr indexer flag '{name}'. Valid names: {string.Join(", ", known.OrderBy(x => x))}"));
}

static RemoteMovie CreateRemoteMovie(
    TestInput input,
    ParsedMovieInfo parsed,
    List<Language> suppliedLanguages,
    IndexerFlags indexerFlags)
{
    Movie movie = null;
    if (input.OriginalLanguage != null)
    {
        if (string.IsNullOrWhiteSpace(input.MediaTitle))
        {
            throw new InvalidOperationException(
                "Radarr remoteRelease input requires mediaTitle when originalLanguage is set");
        }

        movie = new Movie { Title = input.MediaTitle, Year = input.Year };
        movie.MovieMetadata.Value.OriginalLanguage = ResolveLanguages(new[] { input.OriginalLanguage }).Single();
    }

    var remoteMovie = new RemoteMovie
    {
        ParsedMovieInfo = parsed,
        Movie = movie,
        Release = new ReleaseInfo
        {
            Title = input.Title,
            Size = input.SizeBytes,
            IndexerFlags = indexerFlags,
            Languages = input.Languages == null ? new List<Language>() : suppliedLanguages
        }
    };

    new AggregateLanguages(null, LogManager.GetCurrentClassLogger()).Aggregate(remoteMovie);
    return remoteMovie;
}

static CustomFormatInput CreateDiagnosticInput(
    TestInput input,
    ParsedMovieInfo parsed,
    List<Language> languages,
    IndexerFlags indexerFlags)
{
    if (input.Type == "remoteRelease")
    {
        return new CustomFormatInput
        {
            MovieInfo = parsed,
            Size = input.SizeBytes,
            Languages = languages,
            IndexerFlags = indexerFlags
        };
    }

    var movie = new Movie { Title = input.MediaTitle, Year = input.Year };
    if (input.Type == "localFile")
    {
        return new CustomFormatInput
        {
            MovieInfo = new ParsedMovieInfo
            {
                MovieTitles = new List<string> { movie.Title },
                SimpleReleaseTitle = (input.SceneName ?? Path.GetFileName(input.Path)).SimplifyReleaseTitle(),
                ReleaseTitle = input.SceneName,
                Year = movie.Year,
                Quality = parsed.Quality,
                Edition = parsed.Edition,
                Languages = languages,
                ReleaseGroup = parsed.ReleaseGroup
            },
            Movie = movie,
            Size = input.SizeBytes,
            Languages = languages,
            IndexerFlags = indexerFlags,
            Filename = Path.GetFileName(input.Path)
        };
    }

    var releaseTitle = input.SceneName
        ?? Path.GetFileName(input.OriginalFilePath)
        ?? Path.GetFileName(input.RelativePath)
        ?? string.Empty;
    return new CustomFormatInput
    {
        MovieInfo = new ParsedMovieInfo
        {
            MovieTitles = new List<string> { movie.Title },
            SimpleReleaseTitle = releaseTitle.SimplifyReleaseTitle(),
            Year = movie.Year,
            Quality = parsed.Quality,
            Languages = languages,
            ReleaseGroup = parsed.ReleaseGroup,
            Edition = parsed.Edition
        },
        Movie = movie,
        Size = input.SizeBytes,
        Languages = languages,
        IndexerFlags = indexerFlags,
        Filename = Path.GetFileName(input.RelativePath)
    };
}

static object[] BuildDiagnostics(
    IReadOnlyDictionary<string, LoadedFormat> formatByTrashId,
    CustomFormatInput input,
    IReadOnlyCollection<string> missing,
    IReadOnlyCollection<string> unexpected)
{
    return missing.Concat(unexpected).Select(trashId =>
    {
        var loaded = formatByTrashId[trashId];
        return (object)new
        {
            trashId,
            name = loaded.Format.Name,
            expected = missing.Contains(trashId) ? "match" : "doesNotMatch",
            specifications = loaded.Format.Specifications.Select(specification => new
            {
                name = specification.Name,
                implementation = specification.GetType().Name,
                required = specification.Required,
                negate = specification.Negate,
                matched = specification.IsSatisfiedBy(input)
            })
        };
    }).ToArray();
}

static class FormatLoader
{
    public static List<LoadedFormat> Load(string directory)
    {
        var result = new List<LoadedFormat>();
        var nextId = 1;
        foreach (var path in Directory.EnumerateFiles(directory, "*.json").OrderBy(x => x))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            var trashId = root.GetProperty("trash_id").GetString()
                ?? throw new InvalidOperationException($"Missing trash_id in {path}");
            var format = new CustomFormat
            {
                Id = nextId++,
                Name = root.GetProperty("name").GetString(),
                IncludeCustomFormatWhenRenaming = root.GetProperty("includeCustomFormatWhenRenaming").GetBoolean(),
                Specifications = root.GetProperty("specifications").EnumerateArray().Select(LoadSpecification).ToList()
            };
            result.Add(new LoadedFormat(trashId, format, LoadedFormat.ReadScores(root)));
        }
        return result;
    }

    private static ICustomFormatSpecification LoadSpecification(JsonElement source)
    {
        var implementation = source.GetProperty("implementation").GetString();
        var type = typeof(CustomFormat).Assembly.GetType($"NzbDrone.Core.CustomFormats.{implementation}", throwOnError: true);
        var specification = (ICustomFormatSpecification)Activator.CreateInstance(type);
        specification.Name = source.GetProperty("name").GetString();
        specification.Negate = source.GetProperty("negate").GetBoolean();
        specification.Required = source.GetProperty("required").GetBoolean();

        foreach (var field in source.GetProperty("fields").EnumerateObject())
        {
            var property = type.GetProperties(BindingFlags.Instance | BindingFlags.Public)
                .Single(x => string.Equals(x.Name, field.Name, StringComparison.OrdinalIgnoreCase));
            property.SetValue(specification, JsonSerializer.Deserialize(field.Value.GetRawText(), property.PropertyType, Json.Options));
        }
        return specification;
    }
}

sealed class MemoryFormatService : ICustomFormatService
{
    private readonly List<CustomFormat> _formats;
    public MemoryFormatService(List<CustomFormat> formats) => _formats = formats;
    public List<CustomFormat> All() => _formats;
    public CustomFormat GetById(int id) => _formats.Single(x => x.Id == id);
    public CustomFormat Insert(CustomFormat customFormat) => throw new NotSupportedException();
    public void Update(CustomFormat customFormat) => throw new NotSupportedException();
    public void Update(List<CustomFormat> customFormat) => throw new NotSupportedException();
    public void Delete(int id) => throw new NotSupportedException();
    public void Delete(List<int> ids) => throw new NotSupportedException();
}

static class Json
{
    public static JsonSerializerOptions Options { get; } = new() { PropertyNameCaseInsensitive = true };
    public static JsonSerializerOptions Output { get; } = new() { WriteIndented = true };
}
