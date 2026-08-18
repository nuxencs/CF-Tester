using System.Text.Json;
using System.Text.Json.Serialization;
using NzbDrone.Core.CustomFormats;
using NzbDrone.Core.Profiles;
using NzbDrone.Core.Profiles.Qualities;

sealed class GuideProfileCatalog
{
    private readonly IReadOnlyDictionary<string, ProfileDefinition> _profiles;
    private readonly IReadOnlyDictionary<string, LoadedFormat> _formats;

    private GuideProfileCatalog(
        IReadOnlyDictionary<string, ProfileDefinition> profiles,
        IReadOnlyDictionary<string, LoadedFormat> formats)
    {
        _profiles = profiles;
        _formats = formats;
    }

    public static GuideProfileCatalog Load(
        string directory,
        IReadOnlyDictionary<string, LoadedFormat> formats)
    {
        if (!Directory.Exists(directory))
        {
            throw new InvalidOperationException($"Quality profile directory does not exist: {directory}");
        }

        var profiles = new Dictionary<string, ProfileDefinition>();
        foreach (var path in Directory.EnumerateFiles(directory, "*.json").OrderBy(x => x))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            var trashId = root.GetProperty("trash_id").GetString()
                ?? throw new InvalidOperationException($"Missing trash_id in {path}");
            var name = root.GetProperty("name").GetString()
                ?? throw new InvalidOperationException($"Missing name in {path}");
            var scoreSet = root.TryGetProperty("trash_score_set", out var scoreSetProperty)
                ? scoreSetProperty.GetString()
                : null;
            var formatItems = root.GetProperty("formatItems").EnumerateObject()
                .Select(item => new CustomFormatReference
                {
                    TrashId = item.Value.GetString(),
                    Name = item.Name
                })
                .ToArray();
            profiles.Add(trashId, new ProfileDefinition(name, scoreSet, formatItems));
        }

        return new GuideProfileCatalog(profiles, formats);
    }

    public LoadedProfile Build(
        QualityProfileReference reference,
        IEnumerable<CustomFormatReference> additionalFormats)
    {
        if (reference == null)
        {
            throw new InvalidOperationException("A profile score assertion requires a quality profile");
        }
        if (!_profiles.TryGetValue(reference.TrashId, out var definition))
        {
            throw new InvalidOperationException($"Unknown quality profile trashId '{reference.TrashId}'");
        }
        if (definition.Name != reference.Name)
        {
            throw new InvalidOperationException(
                $"Quality profile trashId '{reference.TrashId}' is '{definition.Name}', not '{reference.Name}'");
        }

        var references = definition.FormatItems.Concat(additionalFormats).ToArray();
        var duplicate = references.GroupBy(x => x.TrashId).FirstOrDefault(x => x.Count() > 1);
        if (duplicate != null)
        {
            throw new InvalidOperationException(
                $"Quality profile '{definition.Name}' includes trashId '{duplicate.Key}' more than once");
        }

        var configuredFormats = references.Select(referenceItem =>
        {
            if (!_formats.TryGetValue(referenceItem.TrashId, out var format))
            {
                throw new InvalidOperationException(
                    $"Quality profile '{definition.Name}' uses unknown trashId '{referenceItem.TrashId}'");
            }
            if (format.Format.Name != referenceItem.Name)
            {
                throw new InvalidOperationException(
                    $"Custom Format trashId '{referenceItem.TrashId}' is '{format.Format.Name}', not '{referenceItem.Name}'");
            }

            return new ConfiguredFormat(format, ResolveScore(format.Scores, definition.ScoreSet));
        }).ToArray();

        return new LoadedProfile(
            new QualityProfile
            {
                Name = definition.Name,
                FormatItems = configuredFormats
                    .Select(item => new ProfileFormatItem
                    {
                        Format = item.CustomFormat,
                        Score = item.Score
                    })
                    .ToList()
            },
            configuredFormats);
    }

    public ProfileScoreEvaluation Evaluate(
        TestCase test,
        IReadOnlyCollection<CustomFormat> matchedFormats)
    {
        return test.Expected.ProfileScore == null
            ? null
            : Build(test.Profile, test.AdditionalFormats).Evaluate(
                matchedFormats,
                test.Expected.ProfileScore);
    }

    private static int ResolveScore(IReadOnlyDictionary<string, int> scores, string scoreSet)
    {
        if (scoreSet != null && scores.TryGetValue(scoreSet, out var selectedScore))
        {
            return selectedScore;
        }
        return scores.TryGetValue("default", out var defaultScore) ? defaultScore : 0;
    }
}

sealed class LoadedProfile
{
    private readonly IReadOnlyList<ConfiguredFormat> _formats;

    public LoadedProfile(QualityProfile profile, IReadOnlyList<ConfiguredFormat> formats)
    {
        Profile = profile;
        _formats = formats;
    }

    public QualityProfile Profile { get; }

    public ProfileScoreEvaluation Evaluate(
        IReadOnlyCollection<CustomFormat> matchedFormats,
        ExpectedProfileScore expected)
    {
        var contributions = _formats
            .Where(item => matchedFormats.Contains(item.CustomFormat))
            .Select(item => new ScoreContribution(
                item.TrashId,
                item.Name,
                item.Score))
            .OrderBy(item => item.TrashId)
            .ToArray();
        var actual = Profile.CalculateCustomFormatScore(matchedFormats.ToList());
        var expectedById = expected.Formats.ToDictionary(item => item.TrashId);
        var actualById = contributions.ToDictionary(item => item.TrashId);
        var differences = new List<ScoreDifference>();
        foreach (var trashId in expectedById.Keys.Union(actualById.Keys).OrderBy(x => x))
        {
            expectedById.TryGetValue(trashId, out var expectedItem);
            actualById.TryGetValue(trashId, out var actualItem);
            if (expectedItem != null &&
                actualItem != null &&
                expectedItem.Name == actualItem.Name &&
                expectedItem.Score == actualItem.Score)
            {
                continue;
            }
            differences.Add(new ScoreDifference(
                trashId,
                expectedItem?.Name ?? actualItem.Name,
                expectedItem?.Score,
                actualItem?.Score));
        }

        return new ProfileScoreEvaluation(
            expected.Total,
            actual,
            contributions,
            differences,
            actual == expected.Total && differences.Count == 0);
    }
}

sealed record ProfileDefinition(
    string Name,
    string ScoreSet,
    IReadOnlyList<CustomFormatReference> FormatItems);

sealed record ConfiguredFormat(LoadedFormat Source, int Score)
{
    public CustomFormat CustomFormat => Source.Format;
    public string TrashId => Source.TrashId;
    public string Name => Source.Format.Name;
}

sealed record LoadedFormat(
    string TrashId,
    CustomFormat Format,
    IReadOnlyDictionary<string, int> Scores)
{
    public static IReadOnlyDictionary<string, int> ReadScores(JsonElement source)
    {
        return source.TryGetProperty("trash_scores", out var scores)
            ? scores.EnumerateObject().ToDictionary(x => x.Name, x => x.Value.GetInt32())
            : new Dictionary<string, int>();
    }
}

sealed record ScoreContribution(
    [property: JsonPropertyName("trashId")] string TrashId,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("score")] int Score);

sealed record ScoreDifference(
    [property: JsonPropertyName("trashId")] string TrashId,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("expected")] int? Expected,
    [property: JsonPropertyName("actual")] int? Actual);

sealed record ProfileScoreEvaluation(
    [property: JsonPropertyName("expected")] int Expected,
    [property: JsonPropertyName("actual")] int Actual,
    [property: JsonPropertyName("formats")] IReadOnlyList<ScoreContribution> Formats,
    [property: JsonPropertyName("differences")] IReadOnlyList<ScoreDifference> Differences,
    [property: JsonPropertyName("passed")] bool Passed);
