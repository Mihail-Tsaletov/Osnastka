// Журнал NX для программы «Закладка в оснастку» (Osnastka). C# 5 — совместим с NX 12 / NX 1847 и новее.
//
// Два режима:
//   1) фоновая выгрузка файла (программа запускает сама):
//        run_journal.exe osnastka_nx.cs -args <файл.prt> <папка_вывода>
//   2) кнопка в открытом NX (без параметров): выгружает модель, открытую в NX (обычный NX или Teamcenter —
//      определяется само), и передаёт её программе; если программа не запущена — запускает её.
//      Без кнопки на ленте: Alt+F8 («Воспроизвести журнал») → этот файл → «Выполнить».
//
// Результат в папке вывода: model.json (дерево, цвета, палитра) + meshes/*.stl (геометрия, абсолютные мм).
// Функции, которых может не быть в старых версиях NX, вызываются через отражение с запасным путём.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using NXOpen;
using NXOpen.Assemblies;
using NXOpen.UF;

public class OsnastkaNx
{
    const int FORMAT = 3;   // nx_bridge.EXPORT_FORMAT
    const string TITLE = "Закладка в оснастку";
    static readonly string[] ATTR_DESIGNATION = { "DB_PART_NO", "ОБОЗНАЧЕНИЕ", "Обозначение" };
    static readonly string[] ATTR_TITLE = { "DB_PART_NAME", "НАИМЕНОВАНИЕ", "Наименование" };

    static Session session = Session.GetSession();
    static UFSession ufs;

    string outDir, meshDir;
    int counter = 0;
    List<string> warnings = new List<string>();
    Dictionary<string, int> missing = new Dictionary<string, int>();
    List<string> missingOrder = new List<string>();
    Dictionary<int, bool> usedColors = new Dictionary<int, bool>();

    public static void Main(string[] args)
    {
        try { ufs = UFSession.GetUFSession(); } catch (Exception) { ufs = null; }
        if (args != null && args.Length >= 2 && args[0] == "--open-and-send")
        {
            // для проверки: открыть файл как пользователь и выполнить режим кнопки
            PartLoadStatus st;
            session.Parts.OpenBaseDisplay(args[1], out st);
            st.Dispose();
            FromSession();
        }
        else if (args != null && args.Length >= 2)
            BatchExport(args[0], args[1]);
        else
        {
            try { FromSession(); }
            catch (Exception e) { Message("Не удалось передать модель:\n" + e.Message, true); }
        }
    }

    public static int GetUnloadOption(string dummy)
    {
        return (int)Session.LibraryUnloadOption.Immediately;
    }

    // ======================================================================== режим 1: файл с диска
    static void BatchExport(string prtPath, string outDir)
    {
        prtPath = Path.GetFullPath(prtPath);
        LoadOptions opts = session.Parts.LoadOptions;
        opts.ComponentsToLoad = LoadOptions.LoadComponents.All;
        opts.UsePartialLoading = false;
        TrySet(opts, "UseLightweightRepresentations", false);
        opts.AbortOnFailure = false;
        // компоненты — из папки файла и подпапок; недоступные сетевые библиотеки не опрашиваются
        opts.ComponentLoadMethod = LoadOptions.LoadMethod.SearchDirectories;
        opts.SetSearchDirectories(new string[] { Path.GetDirectoryName(prtPath) }, new bool[] { true });

        PartLoadStatus st;
        BasePart part = session.Parts.OpenBaseDisplay(prtPath, out st);
        st.Dispose();

        OsnastkaNx exp = new OsnastkaNx(outDir);
        string kind = exp.Export(part, prtPath, MTime(prtPath), Path.GetFileNameWithoutExtension(prtPath));
        Console.WriteLine(string.Format("EXPORT_OK {0} {1} nodes, {2} missing, {3} warnings",
            kind, exp.counter, exp.MissingTotal(), exp.warnings.Count));
        for (int i = 0; i < exp.warnings.Count && i < 50; i++)
            Console.WriteLine("WARN " + exp.warnings[i]);
    }

    // ======================================================================== режим 2: кнопка в NX
    static void FromSession()
    {
        BasePart part = session.Parts.Display;
        if (part == null)
        {
            Message("Сначала откройте в NX сборку оснастки (или «шоп»-деталь).", true);
            return;
        }
        string appData = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                      "Osnastka");
        bool managed = IsManaged(part);
        string source, outDir, stem, title;
        double mtime;
        if (managed)
        {
            string ident = part.FullPath.StartsWith("@DB/") ? part.FullPath.Substring(4) : (part.Leaf ?? PartName(part));
            source = "tc:" + ident;
            outDir = Path.Combine(Path.Combine(Path.Combine(appData, "nx"), "models"), Safe(ident));
            mtime = (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
            stem = PartName(part);
            title = ident + " (Teamcenter)";
        }
        else
        {
            source = Path.GetFullPath(part.FullPath);
            stem = Path.GetFileNameWithoutExtension(source);
            // папка модели может быть только для чтения: выгрузка — в профиль, программа перенесёт её в кэш
            outDir = Path.Combine(Path.Combine(Path.Combine(appData, "nx"), "session"), Safe(stem));
            mtime = MTime(source);
            title = Path.GetFileName(source);
        }

        // компоненты, открытые частично или облегчённо, — догрузить полностью
        Status("Закладка в оснастку: догрузка компонентов…");
        try
        {
            MethodInfo m = session.Parts.GetType().GetMethod("EnsurePartsLoadedFully");
            if (m != null)
            {
                object res = m.Invoke(session.Parts, new object[] { new BasePart[] { part }, true });
                IDisposable d = res as IDisposable;
                if (d != null) d.Dispose();
            }
        }
        catch (Exception) { }

        Status("Закладка в оснастку: выгрузка модели…");
        OsnastkaNx exp = new OsnastkaNx(outDir);
        string kind = exp.Export(part, source, mtime, stem);

        // запрос программе
        string inbox = Path.Combine(appData, "inbox");
        Directory.CreateDirectory(inbox);
        StringBuilder sb = new StringBuilder();
        sb.Append("{");
        sb.Append("\"source\": ").Append(Json(source)).Append(", ");
        sb.Append("\"export_dir\": ").Append(Json(outDir)).Append(", ");
        sb.Append("\"managed\": ").Append(managed ? "true" : "false").Append(", ");
        sb.Append("\"kind\": ").Append(Json(kind)).Append(", ");
        sb.Append("\"title\": ").Append(Json(title));
        sb.Append("}");
        string req = Path.Combine(inbox, "request.json");
        File.WriteAllText(req + ".tmp", sb.ToString(), new UTF8Encoding(false));
        if (File.Exists(req)) File.Delete(req);
        File.Move(req + ".tmp", req);

        // программа запущена — она заберёт запрос сама; иначе запустить её
        string launch = Path.Combine(appData, "app_launch.txt");
        if (!File.Exists(launch))
        {
            Message("Модель выгружена, но программа «Закладка в оснастку» ещё ни разу не запускалась на этом " +
                    "компьютере. Запустите Osnastka.exe — модель откроется сама.", false);
            return;
        }
        string[] lines = File.ReadAllLines(launch, Encoding.UTF8);   // pid, программа, аргументы, рабочая папка
        int pid;
        if (lines.Length > 0 && int.TryParse(lines[0].Trim(), out pid) && ProcessAlive(pid))
        {
            Status("Закладка в оснастку: модель передана в программу");
            return;
        }
        if (lines.Length < 2 || !File.Exists(lines[1].Trim()))
        {
            Message("Модель выгружена. Запустите программу «Закладка в оснастку» — модель откроется сама.", false);
            return;
        }
        ProcessStartInfo psi = new ProcessStartInfo(lines[1].Trim(), lines.Length > 2 ? lines[2] : "");
        psi.WorkingDirectory = lines.Length > 3 ? lines[3].Trim() : Path.GetDirectoryName(lines[1].Trim());
        psi.UseShellExecute = true;
        Process.Start(psi);
        Status("Закладка в оснастку: программа запускается…");
    }

    // ======================================================================== выгрузка
    OsnastkaNx(string outDir)
    {
        this.outDir = Path.GetFullPath(outDir);
        meshDir = Path.Combine(this.outDir, "meshes");
        Directory.CreateDirectory(meshDir);
        foreach (string f in Directory.GetFiles(meshDir, "*.stl"))
            File.Delete(f);
    }

    string NewId() { counter++; return "n" + counter; }

    int MissingTotal()
    {
        int s = 0;
        foreach (int v in missing.Values) s += v;
        return s;
    }

    string Export(BasePart displayPart, string source, double mtime, string stem)
    {
        Part dp = displayPart as Part;
        Component root = dp != null && dp.ComponentAssembly != null ? dp.ComponentAssembly.RootComponent : null;
        string kind;
        StringBuilder tree = new StringBuilder();
        if (root != null && root.GetChildren().Length > 0)
        {
            kind = "assembly";
            string rootKey = string.IsNullOrEmpty(root.Name) ? stem : root.Name;
            List<Body> own = SolidBodies(dp);
            VisitComponent(root, rootKey, tree, own);
        }
        else
        {
            kind = "bodies";
            VisitBodies(dp, stem, tree);
        }

        StringBuilder sb = new StringBuilder();
        sb.Append("{\n");
        sb.Append("\"format\": ").Append(FORMAT).Append(",\n");
        sb.Append("\"source\": ").Append(Json(source)).Append(",\n");
        sb.Append("\"source_mtime\": ").Append(mtime.ToString("R", CultureInfo.InvariantCulture)).Append(",\n");
        sb.Append("\"units\": \"mm\",\n");
        sb.Append("\"kind\": ").Append(Json(kind)).Append(",\n");
        sb.Append("\"root\": ").Append(tree.ToString()).Append(",\n");
        sb.Append("\"missing\": [");
        // по убыванию количества
        missingOrder.Sort(delegate(string a, string b) { return missing[b].CompareTo(missing[a]); });
        for (int i = 0; i < missingOrder.Count; i++)
        {
            if (i > 0) sb.Append(", ");
            sb.Append("{\"name\": ").Append(Json(missingOrder[i])).Append(", \"count\": ")
              .Append(missing[missingOrder[i]]).Append("}");
        }
        sb.Append("],\n");
        sb.Append("\"palette\": ").Append(Palette()).Append(",\n");
        sb.Append("\"warnings\": [");
        for (int i = 0; i < warnings.Count; i++)
        {
            if (i > 0) sb.Append(", ");
            sb.Append(Json(warnings[i]));
        }
        sb.Append("]\n}\n");
        File.WriteAllText(Path.Combine(outDir, "model.json"), sb.ToString(), new UTF8Encoding(false));
        return kind;
    }

    void VisitComponent(Component comp, string key, StringBuilder o, List<Body> extraOwnBodies)
    {
        string nodeId = NewId();
        Part proto = comp.Prototype as Part;
        string partName = PartName(proto);
        string name = string.IsNullOrEmpty(comp.Name) ? partName : comp.Name;
        string designation = FirstAttr(comp, ATTR_DESIGNATION);
        if (designation == "") designation = FirstAttr(proto, ATTR_DESIGNATION);
        if (designation == "") designation = partName;
        string title = FirstAttr(comp, ATTR_TITLE);
        if (title == "") title = FirstAttr(proto, ATTR_TITLE);

        List<Body> bodies = SolidBodies(proto);
        List<Body> solids = new List<Body>();
        foreach (Body b in bodies) if (b.IsSolidBody) solids.Add(b);
        bool hasLoadedChildren = false;
        foreach (Component ch in comp.GetChildren())
            if (ch.Prototype is Part) { hasLoadedChildren = true; break; }

        string mesh = null;
        int color = -1;
        StringBuilder children = new StringBuilder();
        int nChildren = 0;
        bool multibody = false;

        if (solids.Count >= 2 && !hasLoadedChildren)
        {
            // многотельная деталь («шоп»-узел внутри сборки): каждое тело — отдельный узел
            multibody = true;
            Dictionary<string, bool> used = new Dictionary<string, bool>();
            for (int i = 0; i < solids.Count; i++)
            {
                Body b = solids[i];
                string tag = BodyTag(b, i);
                while (used.ContainsKey(tag)) tag += "_";
                used[tag] = true;
                string childId = NewId();
                string childMesh = null;
                int childColor = b.Color;
                try
                {
                    Body occ = comp.FindOccurrence(b) as Body;
                    if (occ != null)
                    {
                        childMesh = ExportStl(new List<Body> { occ }, childId + ".stl");
                        childColor = occ.Color;   // цвет вхождения — с учётом переопределения в сборке
                    }
                }
                catch (Exception e) { warnings.Add(key + "/" + tag + ": " + e.Message); }
                if (nChildren++ > 0) children.Append(",\n");
                AppendNode(children, childId, key + "/" + tag, string.IsNullOrEmpty(b.Name) ? tag : b.Name, "",
                           string.IsNullOrEmpty(b.Name) ? tag : b.Name, "", childMesh, childColor, b.Color, true, false, null);
            }
        }
        else
        {
            List<Body> own = new List<Body>();
            if (comp.Parent == null && extraOwnBodies != null)
                own.AddRange(extraOwnBodies);   // тела прямо в файле верхней сборки
            else
            {
                foreach (Body b in bodies)
                {
                    try
                    {
                        Body occ = comp.FindOccurrence(b) as Body;
                        if (occ != null) own.Add(occ);
                    }
                    catch (Exception e) { warnings.Add(key + ": " + e.Message); }
                }
            }
            if (own.Count > 0)
            {
                try
                {
                    mesh = ExportStl(own, nodeId + ".stl");
                    color = DominantColor(own);
                }
                catch (Exception e) { warnings.Add(key + ": " + e.Message); }
            }
        }

        Dictionary<string, int> seen = new Dictionary<string, int>();
        foreach (Component ch in comp.GetChildren())
        {
            if (ch.IsSuppressed) continue;
            if (!(ch.Prototype is Part))   // файл не найден / не загружен — пропускаем
            {
                string mname = string.IsNullOrEmpty(ch.DisplayName) ? ch.Name : ch.DisplayName;
                if (!missing.ContainsKey(mname)) { missing[mname] = 0; missingOrder.Add(mname); }
                missing[mname]++;
                continue;
            }
            int n = seen.ContainsKey(ch.Name) ? seen[ch.Name] + 1 : 1;
            seen[ch.Name] = n;
            string chKey = key + "/" + ch.Name + (n == 1 ? "" : "#" + n);
            if (nChildren++ > 0) children.Append(",\n");
            VisitComponent(ch, chKey, children, null);
        }
        AppendNode(o, nodeId, key, name, partName, designation, title, mesh, color, -1, false, multibody,
                   children.ToString());
    }

    void VisitBodies(Part part, string key, StringBuilder o)
    {
        string rootId = NewId();
        string designation = FirstAttr(part, ATTR_DESIGNATION);
        if (designation == "") designation = PartName(part);
        StringBuilder children = new StringBuilder();
        Dictionary<string, bool> used = new Dictionary<string, bool>();
        int i = 0, n = 0;
        foreach (Body b in part.Bodies)
        {
            if (!b.IsSolidBody) continue;
            string tag = BodyTag(b, i++);
            while (used.ContainsKey(tag)) tag += "_";
            used[tag] = true;
            string id = NewId();
            string mesh = null;
            try { mesh = ExportStl(new List<Body> { b }, id + ".stl"); }
            catch (Exception e) { warnings.Add(tag + ": " + e.Message); }
            if (n++ > 0) children.Append(",\n");
            AppendNode(children, id, key + "/" + tag, string.IsNullOrEmpty(b.Name) ? tag : b.Name, "",
                       string.IsNullOrEmpty(b.Name) ? tag : b.Name, "", mesh, b.Color, -1, true, false, null);
        }
        AppendNode(o, rootId, key, PartName(part), PartName(part), designation, FirstAttr(part, ATTR_TITLE),
                   null, -1, -1, false, false, children.ToString());
    }

    void AppendNode(StringBuilder o, string id, string key, string name, string part, string designation,
                    string title, string mesh, int color, int partColor, bool body, bool multibody, string children)
    {
        o.Append("{\"id\": ").Append(Json(id));
        o.Append(", \"key\": ").Append(Json(key));
        o.Append(", \"name\": ").Append(Json(name));
        o.Append(", \"part\": ").Append(Json(part));
        o.Append(", \"designation\": ").Append(Json(designation));
        o.Append(", \"title\": ").Append(Json(title));
        o.Append(", \"mesh\": ").Append(mesh == null ? "null" : Json(mesh));
        if (color >= 0) { o.Append(", \"color\": ").Append(color); usedColors[color] = true; }
        if (partColor >= 0) { o.Append(", \"part_color\": ").Append(partColor); usedColors[partColor] = true; }
        if (body) o.Append(", \"body\": true");
        if (multibody) o.Append(", \"multibody\": true");
        o.Append(", \"children\": [").Append(children ?? "").Append("]}");
    }

    // ------------------------------------------------------------------------ STL
    string ExportStl(List<Body> bodies, string fileName)
    {
        string full = Path.Combine(meshDir, fileName);
        string err1 = null;
        try { StlCreatorExport(bodies, full); }
        catch (Exception e) { err1 = Inner(e); }
        if (!File.Exists(full))
        {
            try { UfStlExport(bodies, full); }   // запасной путь для старых версий NX
            catch (Exception e)
            {
                throw new Exception("STL: " + (err1 ?? "") + " / " + Inner(e));
            }
        }
        return File.Exists(full) ? fileName : null;
    }

    static void StlCreatorExport(List<Body> bodies, string full)
    {
        PropertyInfo dexProp = typeof(Session).GetProperty("DexManager");
        if (dexProp == null) throw new Exception("нет DexManager");
        object dex = dexProp.GetValue(session, null);
        MethodInfo create = dex.GetType().GetMethod("CreateStlCreator");
        if (create == null) throw new Exception("нет CreateStlCreator");
        object c = create.Invoke(dex, null);
        try
        {
            TrySet(c, "AutoNormalGen", true);
            TrySet(c, "ChordalTol", 0.1);
            TrySet(c, "AdjacencyTol", 0.1);
            PropertyInfo ot = c.GetType().GetProperty("OutputType");
            if (ot != null) ot.SetValue(c, Enum.Parse(ot.PropertyType, "Binary"), null);
            c.GetType().GetProperty("OutputFile").SetValue(c, full, null);
            object block = c.GetType().GetProperty("ExportSelectionBlock").GetValue(c, null);
            MethodInfo add = null;
            foreach (MethodInfo m in block.GetType().GetMethods())
            {
                ParameterInfo[] ps = m.GetParameters();
                if (m.Name == "Add" && ps.Length == 1 && ps[0].ParameterType.IsArray) { add = m; break; }
            }
            Type elem = add.GetParameters()[0].ParameterType.GetElementType();
            Array arr = Array.CreateInstance(elem, bodies.Count);
            for (int i = 0; i < bodies.Count; i++) arr.SetValue(bodies[i], i);
            add.Invoke(block, new object[] { arr });
            c.GetType().GetMethod("Commit").Invoke(c, null);
        }
        finally
        {
            MethodInfo destroy = c.GetType().GetMethod("Destroy");
            if (destroy != null) destroy.Invoke(c, null);
        }
    }

    static void UfStlExport(List<Body> bodies, string full)
    {
        object std = ufs.Std;
        Type t = std.GetType();
        MethodInfo open = t.GetMethod("OpenBinaryStlFile");
        MethodInfo put = t.GetMethod("PutSolidInStlFile");
        MethodInfo close = t.GetMethod("CloseStlFile");
        if (open == null || put == null || close == null) throw new Exception("нет UF STL");
        object[] oa = new object[open.GetParameters().Length];
        oa[0] = full; oa[1] = false; oa[2] = "osnastka";
        open.Invoke(std, oa);
        object fd = oa[oa.Length - 1];
        try
        {
            foreach (Body b in bodies)
            {
                ParameterInfo[] ps = put.GetParameters();
                object[] pa = new object[ps.Length];
                pa[0] = fd; pa[1] = Tag.Null; pa[2] = b.Tag;   // файл, абсолютная СК, тело
                pa[3] = 0.0; pa[4] = 0.0; pa[5] = 0.1;          // мин./макс. ребро (0 — без огр.), допуск
                put.Invoke(std, pa);
            }
        }
        finally { close.Invoke(std, new object[] { fd }); }
    }

    // ------------------------------------------------------------------------ палитра
    string Palette()
    {
        StringBuilder sb = new StringBuilder("{");
        int n = 0;
        MethodInfo ask = ufs != null ? ufs.Disp.GetType().GetMethod("AskColor") : null;
        List<int> colors = new List<int>(usedColors.Keys);
        colors.Sort();
        foreach (int idx in colors)
        {
            try
            {
                ParameterInfo[] ps = ask.GetParameters();
                object[] a = new object[ps.Length];
                a[0] = idx; a[1] = 0;   // номер цвета, модель RGB
                for (int k = 2; k < ps.Length; k++)
                    if (ps[k].ParameterType == typeof(double[])) a[k] = new double[3];
                ask.Invoke(ufs.Disp, a);
                double[] rgb = null;
                foreach (object v in a) if (v is double[]) rgb = (double[])v;
                if (rgb == null) continue;
                if (n++ > 0) sb.Append(", ");
                sb.Append(Json(idx.ToString())).Append(": [")
                  .Append(Math.Round(rgb[0], 4).ToString(CultureInfo.InvariantCulture)).Append(", ")
                  .Append(Math.Round(rgb[1], 4).ToString(CultureInfo.InvariantCulture)).Append(", ")
                  .Append(Math.Round(rgb[2], 4).ToString(CultureInfo.InvariantCulture)).Append("]");
            }
            catch (Exception e)
            {
                if (n == 0 && warnings.Count < 200) warnings.Add("палитра цветов: " + Inner(e));
            }
        }
        return sb.Append("}").ToString();
    }

    // ------------------------------------------------------------------------ помощники
    static bool IsManaged(BasePart part)
    {
        string fp = part.FullPath ?? "";
        if (Environment.GetEnvironmentVariable("OSNASTKA_FORCE_TC") == "1") return true;   // для проверки
        return fp.StartsWith("@DB/") || !File.Exists(fp);
    }

    static string PartName(BasePart part)
    {
        if (part == null) return "";
        string fp = part.FullPath ?? "";
        if (fp.StartsWith("@DB/")) return fp.Substring(4).Split('/')[0];   // номер изделия в Teamcenter
        string n = Path.GetFileNameWithoutExtension(fp);
        return string.IsNullOrEmpty(n) ? (part.Leaf ?? "") : n;
    }

    static string FirstAttr(NXObject obj, string[] titles)
    {
        if (obj == null) return "";
        foreach (string t in titles)
        {
            try
            {
                string v = obj.GetStringAttribute(t);
                if (!string.IsNullOrEmpty(v)) return v;
            }
            catch (Exception) { }
        }
        return "";
    }

    static List<Body> SolidBodies(Part part)
    {
        List<Body> res = new List<Body>();
        if (part == null) return res;
        foreach (Body b in part.Bodies)
            if (b.IsSolidBody || b.IsSheetBody) res.Add(b);
        return res;
    }

    static int DominantColor(List<Body> bodies)
    {
        Dictionary<int, int> cnt = new Dictionary<int, int>();
        int best = -1, bestN = 0;
        foreach (Body b in bodies)
        {
            int c = b.Color;
            cnt[c] = cnt.ContainsKey(c) ? cnt[c] + 1 : 1;
            if (cnt[c] > bestN) { bestN = cnt[c]; best = c; }
        }
        return best;
    }

    static string BodyTag(Body b, int index)
    {
        try
        {
            NXOpen.Features.Feature[] feats = b.GetFeatures();
            if (feats != null && feats.Length > 0)
            {
                Match m = Regex.Match(feats[0].GetFeatureName(), @"\d+");
                if (m.Success) return "Body" + m.Value;
            }
        }
        catch (Exception) { }
        return "Body" + index;
    }

    static string Json(string s)
    {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        foreach (char c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
                    else sb.Append(c);
                    break;
            }
        }
        return sb.Append("\"").ToString();
    }

    static string Safe(string name)
    {
        string s = Regex.Replace(name, @"[^\w.\-]+", "_").Trim('_');
        return s == "" ? "model" : s;
    }

    static double MTime(string path)
    {
        return (File.GetLastWriteTimeUtc(path) - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
    }

    static bool ProcessAlive(int pid)
    {
        try
        {
            Process p = Process.GetProcessById(pid);
            return !p.HasExited;
        }
        catch (Exception) { return false; }
    }

    static void TrySet(object obj, string prop, object value)
    {
        try
        {
            PropertyInfo p = obj.GetType().GetProperty(prop);
            if (p != null && p.CanWrite) p.SetValue(obj, value, null);
        }
        catch (Exception) { }
    }

    static string Inner(Exception e)
    {
        while (e.InnerException != null) e = e.InnerException;
        return e.Message;
    }

    static void Status(string text)
    {
        try
        {
            MethodInfo m = ufs.Ui.GetType().GetMethod("SetStatus");
            if (m != null) m.Invoke(ufs.Ui, new object[] { text });
        }
        catch (Exception) { }
    }

    static void Message(string text, bool error)
    {
        // окно сообщений NX (сборка NXOpenUI) — через отражение: в фоновом NX её нет, тогда только вывод
        try
        {
            Type ui = null;
            foreach (System.Reflection.Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                ui = asm.GetType("NXOpen.UI");
                if (ui != null) break;
            }
            object box = ui.GetProperty("NXMessageBox").GetValue(ui.GetMethod("GetUI").Invoke(null, null), null);
            MethodInfo show = null;
            foreach (MethodInfo m in box.GetType().GetMethods())
                if (m.Name == "Show" && m.GetParameters().Length == 3) { show = m; break; }
            Type kind = show.GetParameters()[1].ParameterType;
            show.Invoke(box, new object[] { TITLE, Enum.Parse(kind, error ? "Error" : "Information"), text });
        }
        catch (Exception) { Console.WriteLine(text); }
    }
}
