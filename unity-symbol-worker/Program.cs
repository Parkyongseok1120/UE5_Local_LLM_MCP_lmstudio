using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Threading;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Operations;

// This process reads source/metadata and runs Roslyn semantics, never Emit, generators,
// project assemblies, build tasks, MSBuild targets, analyzers, models or a planner.
sealed class Source { public string path { get; set; } public string projectPath { get; set; } public string guid { get; set; } }
sealed class AssemblySpec {
    public string name { get; set; } public string outputPath { get; set; }
    public Source[] sources { get; set; } = Array.Empty<Source>();
    public string[] references { get; set; } = Array.Empty<string>();
    public string[] assemblyReferences { get; set; } = Array.Empty<string>();
    public string[] defines { get; set; } = Array.Empty<string>();
    public string[] arguments { get; set; } = Array.Empty<string>();
    public string[] responseFiles { get; set; } = Array.Empty<string>();
    public string[] analyzers { get; set; } = Array.Empty<string>();
    public string languageVersion { get; set; } public bool allowUnsafe { get; set; }
    public string optimization { get; set; }
}
sealed class Manifest {
    public string canonicalProjectRoot { get; set; } public string projectIdentity { get; set; }
    public string editorSessionId { get; set; } public int domainGeneration { get; set; }
    public string compilationId { get; set; } public string manifestId { get; set; }
    public AssemblySpec[] assemblies { get; set; } = Array.Empty<AssemblySpec>();
    public string[] selectedAssemblies { get; set; } = Array.Empty<string>();
}
static class Program {
    static readonly List<object> Symbols = new List<object>(), Uses = new List<object>(), Relations = new List<object>(), Diagnostics = new List<object>(), Missing = new List<object>(), Versions = new List<object>();
    static readonly Dictionary<string, CSharpCompilation> Compilations = new Dictionary<string, CSharpCompilation>();
    static readonly Dictionary<string, string> Hashes = new Dictionary<string, string>();
    static readonly Dictionary<string, Source> Sources = new Dictionary<string, Source>();
    static readonly HashSet<string> Building = new HashSet<string>(), SeenSymbols = new HashSet<string>(), SeenVersions = new HashSet<string>();
    static readonly CancellationTokenSource Deadline = new CancellationTokenSource(TimeSpan.FromSeconds(80));
    static Manifest manifest; static long sourceBytes, metadataBytes; static int sourceCount; static bool incomplete;
    static string Hash(byte[] bytes) { using var sha = SHA256.Create(); return Convert.ToHexString(sha.ComputeHash(bytes)).ToLowerInvariant(); }
    static string Key(ISymbol s) {
        if (s is IAliasSymbol alias) s = alias.Target;
        if (s is IMethodSymbol m && m.ReducedFrom != null) s = m.ReducedFrom;
        s = s.OriginalDefinition;
        var doc = s.GetDocumentationCommentId();
        var location = s.Locations.FirstOrDefault(l => l.IsInSource);
        return (s.ContainingAssembly?.Identity.Name ?? "") + "|" + (doc ?? s.Kind + ":" + s.ToDisplayString() + "@" + location?.SourceTree?.FilePath + ":" + location?.SourceSpan.Start);
    }
    static object Location(Location l) {
        if (l == null || !l.IsInSource) return null;
        var span = l.GetLineSpan(); string physical = l.SourceTree.FilePath;
        Sources.TryGetValue(physical, out var source); Hashes.TryGetValue(physical, out var hash);
        return new { path = source?.projectPath ?? physical, scriptGuid = source?.guid, line = span.StartLinePosition.Line + 1, column = span.StartLinePosition.Character + 1,
            length = l.SourceSpan.Length, sourceHash = hash };
    }
    static void Omit(string kind, string detail) { incomplete = true; if (Missing.Count < 2000) Missing.Add(new { kind, detail }); }
    static void Bound() { Deadline.Token.ThrowIfCancellationRequested(); if (Symbols.Count > 100000 || Uses.Count > 300000 || Relations.Count > 100000) throw new InvalidOperationException("index_entry_limit"); }
    static CSharpCompilation Build(string name) {
        if (Compilations.TryGetValue(name, out var cached)) return cached;
        var spec = manifest.assemblies.FirstOrDefault(a => a.name == name);
        if (spec == null) { Omit("missing_assembly", name); return null; }
        if (!Building.Add(name)) { Omit("assembly_cycle", name); return null; }
        var references = new List<MetadataReference>();
        foreach (var dependency in spec.assemblyReferences) { var built = Build(dependency); if (built != null) references.Add(built.ToMetadataReference()); }
        var sourceOutputs = new HashSet<string>(manifest.assemblies.Select(a => Path.GetFullPath(a.outputPath)), StringComparer.Ordinal);
        foreach (var reference in spec.references.Distinct()) {
            Bound();
            if (sourceOutputs.Contains(Path.GetFullPath(reference))) continue;
            try {
                long size = new FileInfo(reference).Length;
                if (size > 128 * 1024 * 1024 || !SeenVersions.Contains(reference) && metadataBytes + size > 512 * 1024 * 1024) { Omit("reference_budget", reference); continue; }
                references.Add(MetadataReference.CreateFromFile(reference));
                if (SeenVersions.Add(reference)) { var f = new FileInfo(reference); metadataBytes += size; Versions.Add(new { path = reference, kind = "metadata", size = f.Length, mtimeTicks = f.LastWriteTimeUtc.Ticks.ToString(), hash = Hash(File.ReadAllBytes(reference)) }); }
            } catch (Exception e) { Omit("missing_metadata", reference + ":" + e.GetType().Name); }
        }
        // Parse real source arguments and a non-emitted output name: this worker never
        // invokes Emit. Missing command-line sources/output are not project diagnostics.
        var compilerArgs = new List<string> { "/target:library", "/out:" + Path.Combine(manifest.canonicalProjectRoot, "__semantic_only__.dll"), "/langversion:" + (spec.languageVersion ?? "9.0"), spec.allowUnsafe ? "/unsafe+" : "/unsafe-" };
        compilerArgs.AddRange(spec.sources.Select(s => s.path));
        if (spec.defines.Length > 0) compilerArgs.Add("/define:" + String.Join(";", spec.defines));
        compilerArgs.AddRange(spec.arguments);
        // Response files contain compiler switches, never shell commands. Roslyn parses
        // them; no code is run. Track their bytes so the index is version-bound.
        foreach (var response in spec.responseFiles) {
            if (!File.Exists(response)) { Omit("missing_response_file", response); continue; }
            if (new FileInfo(response).Length > 1024 * 1024) { Omit("response_file_budget", response); continue; }
            compilerArgs.Add("@" + response);
            if (SeenVersions.Add(response)) Versions.Add(new { path = response, kind = "compiler_response", hash = Hash(File.ReadAllBytes(response)) });
        }
        var parsed = CSharpCommandLineParser.Default.Parse(compilerArgs, manifest.canonicalProjectRoot, null);
        foreach (var error in parsed.Errors) Omit("compiler_option", error.ToString());
        foreach (var extra in parsed.MetadataReferences) {
            string p = Path.GetFullPath(Path.IsPathRooted(extra.Reference) ? extra.Reference : Path.Combine(manifest.canonicalProjectRoot, extra.Reference));
            try {
                if (sourceOutputs.Contains(p)) { Omit("response_reference_alias_to_source_assembly", p); continue; }
                long size = new FileInfo(p).Length;
                if (size > 128 * 1024 * 1024 || (metadataBytes += size) > 512 * 1024 * 1024) { Omit("metadata_budget", p); continue; }
                references.RemoveAll(r => r.Display == p);
                references.Add(MetadataReference.CreateFromFile(p, extra.Properties));
                if (SeenVersions.Add(p)) Versions.Add(new { path = p, kind = "metadata", hash = Hash(File.ReadAllBytes(p)) });
            } catch (Exception e) { Omit("response_reference_unresolved", p + ":" + e.Message); }
        }
        if (spec.analyzers.Length > 0 || parsed.AnalyzerReferences.Length > 0) Omit("generators_analyzers_not_executed", name);
        var trees = new List<SyntaxTree>();
        foreach (var source in spec.sources) {
            Bound();
            try {
                var before = new FileInfo(source.path); long size = before.Length; var time = before.LastWriteTimeUtc;
                if (++sourceCount > 5000 || size > 4 * 1024 * 1024 || (sourceBytes += size) > 128 * 1024 * 1024) throw new InvalidOperationException("source_budget");
                var bytes = File.ReadAllBytes(source.path); var text = new UTF8Encoding(false, true).GetString(bytes).TrimStart('\uFEFF');
                var hash = Hash(bytes); Hashes[source.path] = hash; Sources[source.path] = source;
                var after = new FileInfo(source.path); if (after.Length != size || after.LastWriteTimeUtc != time) Omit("source_changed_during_read", source.projectPath);
                Versions.Add(new { path = source.path, projectPath = source.projectPath, kind = "source", hash });
                trees.Add(CSharpSyntaxTree.ParseText(text, parsed.ParseOptions, source.path, Encoding.UTF8, Deadline.Token));
            } catch (Exception e) when (!(e is OperationCanceledException)) { Omit("source_unavailable", source.projectPath + ":" + e.Message); }
        }
        var options = parsed.CompilationOptions.WithOutputKind(OutputKind.DynamicallyLinkedLibrary).WithOptimizationLevel(spec.optimization == "Release" ? OptimizationLevel.Release : OptimizationLevel.Debug);
        var compilation = CSharpCompilation.Create(name, trees, references, options);
        Compilations[name] = compilation; Building.Remove(name); return compilation;
    }
    static void Declare(ISymbol symbol) {
        if (symbol == null || symbol.IsImplicitlyDeclared) return;
        string key = Key(symbol); if (!SeenSymbols.Add(key)) return;
        Symbols.Add(new { id = key, name = symbol.Name, display = symbol.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat), kind = symbol.Kind.ToString(),
            assembly = symbol.ContainingAssembly?.Name, containingSymbol = symbol.ContainingSymbol == null ? null : Key(symbol.ContainingSymbol),
            accessibility = symbol.DeclaredAccessibility.ToString(), isStatic = symbol.IsStatic,
            type = symbol is IFieldSymbol f ? f.Type.ToDisplayString() : symbol is IPropertySymbol p ? p.Type.ToDisplayString() : symbol is IMethodSymbol m ? m.ReturnType.ToDisplayString() : null,
            declarations = symbol.Locations.Where(l => l.IsInSource).Select(Location).ToArray() });
        if (symbol is INamedTypeSymbol type) {
            if (type.BaseType != null) Relations.Add(new { kind = "inherits", from = key, to = Key(type.BaseType) });
            foreach (var iface in type.Interfaces) Relations.Add(new { kind = "implements", from = key, to = Key(iface) });
            foreach (var iface in type.AllInterfaces) foreach (var member in iface.GetMembers()) {
                var impl = type.FindImplementationForInterfaceMember(member);
                if (impl != null) Relations.Add(new { kind = "implements_member", from = Key(impl), to = Key(member) });
            }
        }
        if (symbol is IMethodSymbol method && method.OverriddenMethod != null) Relations.Add(new { kind = "overrides", from = key, to = Key(method.OverriddenMethod) });
        if (symbol is IPropertySymbol property && property.OverriddenProperty != null) Relations.Add(new { kind = "overrides", from = key, to = Key(property.OverriddenProperty) });
        if (symbol is IEventSymbol ev && ev.OverriddenEvent != null) Relations.Add(new { kind = "overrides", from = key, to = Key(ev.OverriddenEvent) });
    }
    static void Analyze(CSharpCompilation compilation) {
        foreach (var diagnostic in compilation.GetDiagnostics(Deadline.Token)) {
            if (diagnostic.Severity == DiagnosticSeverity.Error) incomplete = true;
            if (Diagnostics.Count < 1000) Diagnostics.Add(new { id = diagnostic.Id, severity = diagnostic.Severity.ToString(), message = diagnostic.GetMessage(), location = Location(diagnostic.Location) });
            else Omit("diagnostics_truncated", compilation.AssemblyName);
        }
        foreach (var tree in compilation.SyntaxTrees) {
            var model = compilation.GetSemanticModel(tree, true);
            foreach (var node in tree.GetRoot(Deadline.Token).DescendantNodesAndSelf()) {
                Bound();
                if (node is BaseTypeDeclarationSyntax || node is DelegateDeclarationSyntax || node is BaseMethodDeclarationSyntax || node is BasePropertyDeclarationSyntax || node is VariableDeclaratorSyntax || node is ParameterSyntax ||
                    node is EnumMemberDeclarationSyntax || node is LocalFunctionStatementSyntax || node is AccessorDeclarationSyntax || node is TypeParameterSyntax || node is SingleVariableDesignationSyntax || node is ForEachStatementSyntax || node is CatchDeclarationSyntax)
                    Declare(model.GetDeclaredSymbol(node, Deadline.Token));
                // These explicit operations have no SimpleNameSyntax for their target.
                if (node is BaseObjectCreationExpressionSyntax || node is BinaryExpressionSyntax || node is PrefixUnaryExpressionSyntax || node is PostfixUnaryExpressionSyntax || node is CastExpressionSyntax || node is AssignmentExpressionSyntax || node is ElementAccessExpressionSyntax || node is ElementBindingExpressionSyntax || node is ConstructorInitializerSyntax || node is AttributeSyntax) {
                    var operation = model.GetOperation(node, Deadline.Token);
                    ISymbol called = operation is IObjectCreationOperation create ? create.Constructor : operation is IBinaryOperation binary ? binary.OperatorMethod : operation is IUnaryOperation unary ? unary.OperatorMethod :
                        operation is IIncrementOrDecrementOperation increment ? increment.OperatorMethod : operation is IConversionOperation conversion ? conversion.OperatorMethod : operation is ICompoundAssignmentOperation compound ? compound.OperatorMethod :
                        operation is IPropertyReferenceOperation property ? property.Property : operation is IInvocationOperation invocation ? invocation.TargetMethod : null;
                    if (called == null && (node is ConstructorInitializerSyntax || node is AttributeSyntax)) called = model.GetSymbolInfo(node, Deadline.Token).Symbol;
                    if (called != null) { var owner = model.GetEnclosingSymbol(node.SpanStart, Deadline.Token); Uses.Add(new { kind = "code_use", from = owner == null ? null : Key(owner), to = Key(called), location = Location(node.GetLocation()) }); }
                    else if (operation is IInvalidOperation) Omit("unresolved_operation", tree.FilePath + ":" + node.SpanStart + ":" + node.Kind());
                }
                if (!(node is SimpleNameSyntax)) continue;
                var info = model.GetSymbolInfo(node, Deadline.Token);
                if (info.Symbol != null) {
                    var owner = model.GetEnclosingSymbol(node.SpanStart, Deadline.Token);
                    Uses.Add(new { kind = "code_use", from = owner == null ? null : Key(owner), to = Key(info.Symbol), location = Location(node.GetLocation()) });
                } else if (info.CandidateSymbols.Length > 0 || node.Parent is MemberAccessExpressionSyntax || node.Parent is InvocationExpressionSyntax)
                    Omit("unresolved_use", tree.FilePath + ":" + node.SpanStart + ":" + info.CandidateReason);
            }
        }
    }
    static int Main() {
        try {
            string input = Console.In.ReadToEnd(); if (input.Length > 16 * 1024 * 1024) throw new InvalidOperationException("manifest_budget");
            manifest = JsonSerializer.Deserialize<Manifest>(input);
            if (manifest == null || manifest.assemblies.Length > 256 || manifest.selectedAssemblies.Length == 0) throw new InvalidOperationException("invalid_manifest");
            foreach (var selected in manifest.selectedAssemblies) { var c = Build(selected); if (c != null) Analyze(c); }
            var result = new { status = "indexed", workerVersion = "1.4.0-alpha.2", compilerVersion = typeof(CSharpCompilation).Assembly.GetName().Version.ToString(),
                indexVersion = Guid.NewGuid().ToString("N"), manifest.manifestId, manifest.projectIdentity, manifest.editorSessionId, manifest.domainGeneration, manifest.compilationId,
                observedAt = DateTime.UtcNow.ToString("O"), completeWithinScope = !incomplete, scope = manifest.selectedAssemblies,
                referenceCoverage = "explicit names plus compiler-bound construction, constructor initializers, attributes, operators, conversions and indexers; implicit/lowered operations (foreach/await/disposal and implicit conversions), dynamic dispatch and generated sources are not enumerated",
                omissions = Missing, diagnostics = Diagnostics, symbols = Symbols, uses = Uses, relations = Relations, versions = Versions };
            Console.WriteLine(JsonSerializer.Serialize(result)); return 0;
        } catch (Exception e) { Console.Error.WriteLine(e.GetType().Name + ": " + e.Message); return 1; }
    }
}
