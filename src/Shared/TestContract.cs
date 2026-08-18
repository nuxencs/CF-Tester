using System.Text.Json;

static class TestContract
{
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public static IEnumerable<TestCase> LoadCases(IEnumerable<string> paths, string application)
    {
        foreach (var path in paths)
        {
            var suite = JsonSerializer.Deserialize<TestSuite>(File.ReadAllText(path), Options)
                ?? throw new InvalidOperationException($"Could not read case file {path}");

            if (suite.SchemaVersion != 1)
            {
                throw new InvalidOperationException(
                    $"{path}: unsupported schemaVersion {suite.SchemaVersion}; expected 1");
            }

            if (suite.Application != application)
            {
                throw new InvalidOperationException(
                    $"{path}: application must be '{application}', not '{suite.Application}'");
            }

            foreach (var test in suite.Cases)
            {
                yield return test;
            }
        }
    }

    public static void ValidateReferences(
        TestCase test,
        IReadOnlyDictionary<string, string> formatNames,
        string application)
    {
        var references = test.Expected.Matches
            .Concat(test.Expected.DoesNotMatch)
            .Concat(test.AdditionalFormats)
            .Concat(test.Expected.ProfileScore?.Formats ?? Array.Empty<ScoredFormatReference>());
        foreach (var reference in references)
        {
            if (!formatNames.TryGetValue(reference.TrashId, out var formatName))
            {
                throw new InvalidOperationException(
                    $"{test.Name}: unknown {application} trashId '{reference.TrashId}'");
            }

            if (reference.Name != formatName)
            {
                throw new InvalidOperationException(
                    $"{test.Name}: trashId '{reference.TrashId}' is '{formatName}', not '{reference.Name}'");
            }
        }
    }
}

sealed class TestSuite
{
    public int SchemaVersion { get; set; }
    public string Application { get; set; }
    public List<TestCase> Cases { get; set; } = new();
}

sealed class TestCase
{
    public string Name { get; set; }
    public string Source { get; set; }
    public QualityProfileReference Profile { get; set; }
    public CustomFormatReference[] AdditionalFormats { get; set; } = Array.Empty<CustomFormatReference>();
    public TestInput Input { get; set; } = new();
    public ExpectedResult Expected { get; set; } = new();
}

sealed class TestInput
{
    public string Type { get; set; }
    public string Title { get; set; }
    public string Path { get; set; }
    public string RelativePath { get; set; }
    public string OriginalFilePath { get; set; }
    public string SceneName { get; set; }
    public string MediaTitle { get; set; }
    public string OriginalLanguage { get; set; }
    public string[] EpisodeTitles { get; set; }
    public int Year { get; set; }
    public long SizeBytes { get; set; }
    public string[] IndexerFlags { get; set; }
    public string[] Languages { get; set; }
}

sealed class ExpectedResult
{
    public CustomFormatReference[] Matches { get; set; } = Array.Empty<CustomFormatReference>();
    public CustomFormatReference[] DoesNotMatch { get; set; } = Array.Empty<CustomFormatReference>();
    public ExpectedProfileScore ProfileScore { get; set; }
}

class CustomFormatReference
{
    public string TrashId { get; set; }
    public string Name { get; set; }
}

sealed class ScoredFormatReference : CustomFormatReference
{
    public int Score { get; set; }
}

sealed class QualityProfileReference
{
    public string TrashId { get; set; }
    public string Name { get; set; }
}

sealed class ExpectedProfileScore
{
    public int Total { get; set; }
    public ScoredFormatReference[] Formats { get; set; } = Array.Empty<ScoredFormatReference>();
}
