// Office Setup Tool (GUI)
// Removal : Office Scrubber v14 Fast (embedded, or the "Scrubber" folder next to the exe)
// Install : Microsoft Office Deployment Tool (setup.exe from officecdn.microsoft.com)
// No activation tricks: install, then sign in or enter your own product key.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using System.Xml;

namespace OfficeSetupTool
{
    // ------------------------------------------------------------------ catalog
    public class ProductDef
    {
        public string Id, Name, Family;
        public ProductDef(string id, string name, string family) { Id = id; Name = name; Family = family; }
        public string Channel
        {
            get
            {
                if (Family == "V24") return "PerpetualVL2024";
                if (Family == "V21") return "PerpetualVL2021";
                return "Current";
            }
        }
        public string Visio { get { return Map("VisioPro"); } }
        public string Project { get { return Map("ProjectPro"); } }
        string Map(string p)
        {
            switch (Family)
            {
                case "R24": return p + "2024Retail";
                case "V24": return p + "2024Volume";
                case "R21": return p + "2021Retail";
                case "V21": return p + "2021Volume";
                default: return p + "Retail";
            }
        }
        public override string ToString() { return Name; }
    }

    public class Item
    {
        public string Id, Name; public bool On;
        public Item(string id, string name, bool on) { Id = id; Name = name; On = on; }
        public override string ToString() { return Name; }
    }

    public static class Catalog
    {
        public static readonly ProductDef[] Products = {
            new ProductDef("O365ProPlusRetail",      "Microsoft 365 Apps for enterprise", "365"),
            new ProductDef("O365BusinessRetail",     "Microsoft 365 Apps for business", "365"),
            new ProductDef("O365HomePremRetail",     "Microsoft 365 Family / Personal", "365"),
            new ProductDef("Home2024Retail",         "Office Home 2024", "R24"),
            new ProductDef("HomeBusiness2024Retail", "Office Home & Business 2024", "R24"),
            new ProductDef("ProPlus2024Retail",      "Office Professional Plus 2024 (קמעונאי)", "R24"),
            new ProductDef("ProPlus2024Volume",      "Office LTSC Professional Plus 2024 (רישוי ארגוני)", "V24"),
            new ProductDef("Standard2024Volume",     "Office LTSC Standard 2024 (רישוי ארגוני)", "V24"),
            new ProductDef("HomeBusiness2021Retail", "Office Home & Business 2021 (התמיכה הסתיימה)", "R21"),
            new ProductDef("ProPlus2021Retail",      "Office Professional Plus 2021 (התמיכה הסתיימה)", "R21"),
            new ProductDef("ProPlus2021Volume",      "Office LTSC Professional Plus 2021 (התמיכה הסתיימה)", "V21"),
        };

        public static readonly Item[] Languages = {
            new Item("he-il", "עברית", true),
            new Item("en-us", "אנגלית", true),
            new Item("ar-sa", "ערבית", false),
            new Item("ru-ru", "רוסית", false),
            new Item("fr-fr", "צרפתית", false),
            new Item("es-es", "ספרדית", false),
            new Item("de-de", "גרמנית", false),
            new Item("MatchOS", "כמו שפת Windows", false),
        };

        // ExcludeApp IDs + Visio/Project (separate products)
        public static readonly Item[] Apps = {
            new Item("Word", "Word", true),
            new Item("Excel", "Excel", true),
            new Item("PowerPoint", "PowerPoint", true),
            new Item("Outlook", "Outlook (קלאסי)", true),
            new Item("OutlookForWindows", "Outlook (החדש)", false),
            new Item("Access", "Access", true),
            new Item("Publisher", "Publisher (הופסק ע\"י Microsoft ב-2026)", false),
            new Item("OneNote", "OneNote", false),
            new Item("OneDrive", "OneDrive", false),
            new Item("Teams", "Teams", false),
            new Item("Lync", "Skype for Business", false),
            new Item("Groove", "OneDrive for Business (לקוח סנכרון ישן)", false),
            new Item("Visio", "Visio (דורש רישיון נפרד)", false),
            new Item("Project", "Project (דורש רישיון נפרד)", false),
        };

        public static readonly string[] ExcludableApps = {
            "Word", "Excel", "PowerPoint", "Outlook", "OutlookForWindows", "Access",
            "Publisher", "OneNote", "OneDrive", "Teams", "Lync", "Groove" };
    }

    public class Options
    {
        public ProductDef Product;
        public int Bit = 64;
        public List<string> Langs = new List<string>();
        public HashSet<string> Apps = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public string Key = "";

        public string FolderName()
        {
            return Product.Id + "_x" + Bit + "_" + string.Join("_", Langs.ToArray());
        }
    }

    // ------------------------------------------------------------------ config.xml
    public static class ConfigBuilder
    {
        public static readonly Regex KeyRx = new Regex("^[0-9A-Za-z]{5}(-[0-9A-Za-z]{5}){4}$");

        public static string Build(Options o, string sourcePath, bool includeKey)
        {
            var sb = new StringBuilder();
            sb.Append("<Configuration>\r\n");
            sb.Append("  <Add OfficeClientEdition=\"").Append(o.Bit).Append("\" Channel=\"").Append(o.Product.Channel).Append("\"");
            if (!string.IsNullOrEmpty(sourcePath)) sb.Append(" SourcePath=\"").Append(Esc(sourcePath)).Append("\"");
            sb.Append(" AllowCdnFallback=\"TRUE\">\r\n");
            AppendProduct(sb, o, o.Product.Id, includeKey ? o.Key : "");
            if (o.Apps.Contains("Visio")) AppendProduct(sb, o, o.Product.Visio, "");
            if (o.Apps.Contains("Project")) AppendProduct(sb, o, o.Product.Project, "");
            sb.Append("  </Add>\r\n");
            sb.Append("  <Property Name=\"FORCEAPPSHUTDOWN\" Value=\"TRUE\" />\r\n");
            sb.Append("  <Updates Enabled=\"TRUE\" />\r\n");
            sb.Append("  <RemoveMSI />\r\n");
            sb.Append("  <Display Level=\"Full\" AcceptEULA=\"TRUE\" />\r\n");
            sb.Append("</Configuration>\r\n");
            return sb.ToString();
        }

        static void AppendProduct(StringBuilder sb, Options o, string id, string key)
        {
            sb.Append("    <Product ID=\"").Append(id).Append("\"");
            if (!string.IsNullOrEmpty(key)) sb.Append(" PIDKEY=\"").Append(Esc(key.ToUpperInvariant())).Append("\"");
            sb.Append(">\r\n");
            foreach (var l in o.Langs) sb.Append("      <Language ID=\"").Append(l).Append("\" />\r\n");
            foreach (var a in Catalog.ExcludableApps)
                if (!o.Apps.Contains(a)) sb.Append("      <ExcludeApp ID=\"").Append(a).Append("\" />\r\n");
            if (o.Product.Family == "365") sb.Append("      <ExcludeApp ID=\"Bing\" />\r\n");
            sb.Append("    </Product>\r\n");
        }

        static string Esc(string s) { return SecurityElement.Escape(s); }

        public static void SetSourcePath(string inFile, string outFile, string sourcePath)
        {
            var doc = new XmlDocument();
            doc.Load(inFile);
            var add = (XmlElement)doc.SelectSingleNode("/Configuration/Add");
            if (add == null) throw new Exception("config.xml לא תקין");
            add.SetAttribute("SourcePath", sourcePath);
            doc.Save(outFile);
        }
    }

    // ------------------------------------------------------------------ engine
    public class Engine
    {
        public const string OdtUrl = "https://officecdn.microsoft.com/pr/wsus/setup.exe";
        public Action<string> Log = delegate { };
        public string BaseDir, WorkDir;

        public Engine()
        {
            BaseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            WorkDir = Path.Combine(Path.GetTempPath(), "OfficeSetupTool");
            Directory.CreateDirectory(WorkDir);
        }

        public string DataDir
        {
            get
            {
                // next to the exe when writable, otherwise ProgramData
                try
                {
                    string t = Path.Combine(BaseDir, ".write-test");
                    File.WriteAllText(t, "x"); File.Delete(t);
                    return BaseDir;
                }
                catch
                {
                    string d = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "OfficeSetupTool");
                    Directory.CreateDirectory(d);
                    return d;
                }
            }
        }

        public string FilesDir { get { return Path.Combine(DataDir, "Office_Files"); } }

        // ---- removal
        public string ScrubberPath()
        {
            string local = Path.Combine(BaseDir, @"Scrubber\OfficeScrubber.cmd");
            if (File.Exists(local)) return local;

            string root = Path.Combine(WorkDir, "Scrubber");
            var asm = Assembly.GetExecutingAssembly();
            foreach (string res in asm.GetManifestResourceNames())
            {
                if (!res.StartsWith("Scrubber/")) continue;
                string dest = Path.Combine(WorkDir, res.Replace('/', '\\'));
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                using (var s = asm.GetManifestResourceStream(res))
                using (var f = File.Create(dest)) s.CopyTo(f);
            }
            string p = Path.Combine(root, "OfficeScrubber.cmd");
            if (!File.Exists(p)) throw new Exception("קבצי כלי ההסרה חסרים.");
            return p;
        }

        public bool Scrub(string args)
        {
            string cmd = ScrubberPath();
            string runner = Path.Combine(Path.GetDirectoryName(cmd), "RunScrubber.ps1");
            if (!File.Exists(runner)) throw new Exception("RunScrubber.ps1 חסר.");
            Log("מסיר את Office... (ההתקדמות מוצגת בחלון הכחול. אין צורך ללחוץ בתוכו)");
            // RunScrubber.ps1 turns QuickEdit off, so a click cannot freeze the window
            var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"),
                "-NoProfile -ExecutionPolicy Bypass -File \"" + runner + "\" " + args);
            psi.UseShellExecute = false;
            psi.WorkingDirectory = Path.GetDirectoryName(cmd);
            using (var p = Process.Start(psi)) p.WaitForExit();
            Log("ההסרה הסתיימה. מומלץ להפעיל מחדש את המחשב לפני התקנה חדשה (לא חובה).");
            return true;
        }

        // ---- Office Deployment Tool
        public string OdtPath { get { return Path.Combine(DataDir, @"ODT\setup.exe"); } }

        public string GetOdt()
        {
            string odt = OdtPath;
            if (File.Exists(odt))
            {
                if (IsMicrosoftSigned(odt)) return odt;
                File.Delete(odt);
            }
            Directory.CreateDirectory(Path.GetDirectoryName(odt));
            Log("מוריד את Office Deployment Tool מהאתר של Microsoft...");
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072; // TLS 1.2
            using (var wc = new WebClient()) wc.DownloadFile(OdtUrl, odt);
            if (!IsMicrosoftSigned(odt))
            {
                File.Delete(odt);
                throw new Exception("הקובץ שהורד אינו חתום ע\"י Microsoft ולכן נמחק.");
            }
            Log("Office Deployment Tool מוכן (חתימה דיגיטלית של Microsoft אומתה).");
            return odt;
        }

        public static bool IsMicrosoftSigned(string file)
        {
            string ps = "$s=Get-AuthenticodeSignature -LiteralPath $env:OST_FILE; " +
                        "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'O=Microsoft Corporation') { exit 0 }; exit 1";
            var psi = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -Command \"" + ps + "\"");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["OST_FILE"] = file;
            using (var p = Process.Start(psi)) { p.WaitForExit(); return p.ExitCode == 0; }
        }

        int RunOdt(string verb, string cfg)
        {
            string odt = GetOdt();
            var psi = new ProcessStartInfo(odt, verb + " \"" + cfg + "\"");
            psi.UseShellExecute = false;
            psi.WorkingDirectory = Path.GetDirectoryName(odt);
            using (var p = Process.Start(psi)) { p.WaitForExit(); return p.ExitCode; }
        }

        // ---- install / download
        public bool InstallOnline(Options o)
        {
            string cfg = Path.Combine(WorkDir, "config-online.xml");
            File.WriteAllText(cfg, ConfigBuilder.Build(o, null, true));
            Log("מתקין את " + o.Product.Name + " (" + o.Bit + "-bit) מהשרתים של Microsoft...");
            Log("חלון ההתקנה של Microsoft יציג את ההתקדמות.");
            int rc = RunOdt("/configure", cfg);
            try { File.Delete(cfg); } catch { }
            return Report(rc, o.Product.Id);
        }

        public bool Download(Options o)
        {
            string dir = Path.Combine(FilesDir, o.FolderName());
            Directory.CreateDirectory(dir);
            string cfg = Path.Combine(dir, "config.xml");
            File.WriteAllText(cfg, ConfigBuilder.Build(o, dir, false)); // never store the key on disk
            Log("מוריד את " + o.Product.Name + " אל:");
            Log("  " + dir);
            Log("זה יכול לקחת זמן (כמה GB).");
            int rc = RunOdt("/download", cfg);
            bool ok = Directory.Exists(Path.Combine(dir, @"Office\Data"));
            Log(ok ? "ההורדה הסתיימה. אפשר להתקין מהקבצים בלחיצה על \"התקנה מקבצים שהורדו\"."
                   : "שגיאה: ההורדה לא הושלמה (קוד " + rc + ").");
            return ok;
        }

        public bool InstallOffline(string dir)
        {
            string src = Path.Combine(dir, "config.xml");
            string cfg = Path.Combine(WorkDir, "config-offline.xml");
            ConfigBuilder.SetSourcePath(src, cfg, dir);
            Log("מתקין מהקבצים שב-" + dir);
            int rc = RunOdt("/configure", cfg);
            return Report(rc, null);
        }

        bool Report(int rc, string productId)
        {
            string ids = InstalledReleaseIds();
            bool ok = ids != null && (productId == null || ids.IndexOf(productId, StringComparison.OrdinalIgnoreCase) >= 0);
            if (ok)
            {
                Log("Office הותקן בהצלחה (קוד " + rc + ").");
                Log("להפעלה: פתח את Word והתחבר לחשבון Microsoft שלך, או הזן מפתח מוצר בכפתור \"רישוי והפעלה\".");
            }
            else
            {
                Log("שגיאה: Office לא הותקן (קוד " + rc + ").");
                Log("קבצי לוג: בתיקיות %TEMP% ו-%WINDIR%\\Temp (בשם המחשב).");
            }
            return ok;
        }

        public static string InstalledReleaseIds()
        {
            using (var k = Microsoft.Win32.RegistryKey.OpenBaseKey(Microsoft.Win32.RegistryHive.LocalMachine, Microsoft.Win32.RegistryView.Registry64)
                .OpenSubKey(@"SOFTWARE\Microsoft\Office\ClickToRun\Configuration"))
            {
                return k == null ? null : k.GetValue("ProductReleaseIds") as string;
            }
        }

        // ---- licensing (ospp.vbs)
        public static string OsppPath()
        {
            foreach (var pf in new[] {
                Environment.GetEnvironmentVariable("ProgramW6432"),
                Environment.GetEnvironmentVariable("ProgramFiles"),
                Environment.GetEnvironmentVariable("ProgramFiles(x86)") })
            {
                if (string.IsNullOrEmpty(pf)) continue;
                string p = Path.Combine(pf, @"Microsoft Office\Office16\OSPP.VBS");
                if (File.Exists(p)) return p;
            }
            return null;
        }

        public void Ospp(string args)
        {
            string ospp = OsppPath();
            if (ospp == null) { Log("Office 2016 ומעלה אינו מותקן."); return; }
            var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "cscript.exe"),
                "//nologo \"" + ospp + "\" " + args);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            try
            {
                psi.StandardOutputEncoding = Encoding.GetEncoding(System.Globalization.CultureInfo.CurrentCulture.TextInfo.OEMCodePage);
            }
            catch { }
            using (var p = Process.Start(psi))
            {
                string o = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
                p.WaitForExit();
                foreach (var line in o.Replace("\r", "").Split('\n'))
                    if (line.Trim().Length > 0) Log(line);
            }
        }
    }

    // ------------------------------------------------------------------ UI
    public class MainForm : Form
    {
        readonly Engine eng = new Engine();
        ComboBox cbProduct;
        RadioButton rb64, rb32;
        CheckedListBox clLang, clApps;
        Label lblMainLang;
        TextBox tbKey, tbLog;
        ProgressBar bar;
        readonly List<Button> actions = new List<Button>();
        readonly List<string> langOrder = new List<string>();

        public MainForm()
        {
            Text = "Office Setup Tool - הסרה והתקנה רשמית של Office";
            RightToLeft = RightToLeft.Yes;
            RightToLeftLayout = true;
            Font = new Font("Segoe UI", 9.5f);
            AutoScaleMode = AutoScaleMode.Font;
            ClientSize = new Size(900, 700);
            MinimumSize = new Size(916, 739);
            StartPosition = FormStartPosition.CenterScreen;
            eng.Log = LogLine;

            // --- settings group
            var gSet = new GroupBox { Text = "הגדרות התקנה", Location = new Point(12, 10), Size = new Size(560, 440) };
            Controls.Add(gSet);

            gSet.Controls.Add(new Label { Text = "מוצר (לפי הרישיון שיש לך):", Location = new Point(12, 28), AutoSize = true });
            cbProduct = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Location = new Point(12, 50), Size = new Size(536, 28) };
            cbProduct.Items.AddRange(Catalog.Products);
            cbProduct.SelectedIndex = 0;
            gSet.Controls.Add(cbProduct);

            gSet.Controls.Add(new Label { Text = "גרסה:", Location = new Point(12, 88), AutoSize = true });
            rb64 = new RadioButton { Text = "64-bit (מומלץ)", Location = new Point(70, 86), AutoSize = true, Checked = true };
            rb32 = new RadioButton { Text = "32-bit (רק לתוספים ישנים)", Location = new Point(210, 86), AutoSize = true };
            gSet.Controls.Add(rb64); gSet.Controls.Add(rb32);
            if (!Environment.Is64BitOperatingSystem) { rb32.Checked = true; rb64.Enabled = false; }

            gSet.Controls.Add(new Label { Text = "שפות:", Location = new Point(12, 120), AutoSize = true });
            clLang = new CheckedListBox { Location = new Point(12, 142), Size = new Size(200, 190), CheckOnClick = true };
            foreach (var l in Catalog.Languages)
            {
                int i = clLang.Items.Add(l);
                if (l.On) { clLang.SetItemChecked(i, true); langOrder.Add(l.Id); }
            }
            clLang.ItemCheck += OnLangCheck;
            gSet.Controls.Add(clLang);
            lblMainLang = new Label { Location = new Point(12, 338), Size = new Size(200, 40) };
            gSet.Controls.Add(lblMainLang);
            UpdateMainLang();

            gSet.Controls.Add(new Label { Text = "יישומים להתקנה:", Location = new Point(228, 120), AutoSize = true });
            clApps = new CheckedListBox { Location = new Point(228, 142), Size = new Size(320, 236), CheckOnClick = true };
            foreach (var a in Catalog.Apps) clApps.SetItemChecked(clApps.Items.Add(a), a.On);
            gSet.Controls.Add(clApps);

            gSet.Controls.Add(new Label { Text = "מפתח מוצר (לא חובה - אפשר גם אחרי ההתקנה):", Location = new Point(12, 384), AutoSize = true });
            tbKey = new TextBox { Location = new Point(12, 404), Size = new Size(320, 26), RightToLeft = RightToLeft.No, CharacterCasing = CharacterCasing.Upper, MaxLength = 29 };
            gSet.Controls.Add(tbKey);

            // --- actions group
            var gAct = new GroupBox { Text = "פעולות", Location = new Point(584, 10), Size = new Size(304, 440) };
            Controls.Add(gAct);
            int y = 26;
            AddAction(gAct, ref y, "הסרה מלאה של Office", "מסיר את כל גרסאות Office שזוהו, כולל רישיונות", DoRemove);
            AddAction(gAct, ref y, "התקנה מהאינטרנט", "מתקין ישירות מהשרתים של Microsoft", DoInstall);
            AddAction(gAct, ref y, "הסרה מלאה + התקנה נקייה", "מסיר הכל ואז מתקין מחדש", DoReinstall);
            AddAction(gAct, ref y, "הורדה להתקנה בלי אינטרנט", "שומר את קבצי ההתקנה בתיקייה Office_Files", DoDownload);
            AddAction(gAct, ref y, "התקנה מקבצים שהורדו", "מתקין מתיקייה שהורדה קודם", DoOffline);
            AddAction(gAct, ref y, "רישוי והפעלה", "הזנת מפתח, מצב רישוי, התחברות לחשבון", DoLicense);
            AddAction(gAct, ref y, "הסרה מתקדמת", "סריקה עמוקה / התפריט המלא של כלי ההסרה", DoAdvanced);

            // --- log
            bar = new ProgressBar { Location = new Point(12, 460), Size = new Size(876, 10), Style = ProgressBarStyle.Marquee, Visible = false, Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
            Controls.Add(bar);
            tbLog = new TextBox
            {
                Location = new Point(12, 476), Size = new Size(876, 212), Multiline = true, ReadOnly = true,
                ScrollBars = ScrollBars.Vertical, BackColor = Color.White,
                Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
            };
            Controls.Add(tbLog);

            LogLine("ברוך הבא. בחר הגדרות ופעולה.");
            LogLine("ההתקנה נעשית רק מהשרתים הרשמיים של Microsoft. ההפעלה (רישוי) נעשית על ידך עם רישיון חוקי.");
            string ids = Engine.InstalledReleaseIds();
            if (ids != null) LogLine("מותקן כרגע: " + ids);
        }

        void AddAction(GroupBox g, ref int y, string text, string tip, Action handler)
        {
            var b = new Button { Text = text, Location = new Point(14, y), Size = new Size(276, 40) };
            var t = new ToolTip(); t.SetToolTip(b, tip);
            b.Click += delegate { handler(); };
            g.Controls.Add(b);
            actions.Add(b);
            y += 56;
        }

        void OnLangCheck(object sender, ItemCheckEventArgs e)
        {
            string id = ((Item)clLang.Items[e.Index]).Id;
            if (e.NewValue == CheckState.Checked) { if (!langOrder.Contains(id)) langOrder.Add(id); }
            else langOrder.Remove(id);
            UpdateMainLang();
        }

        void UpdateMainLang()
        {
            if (langOrder.Count == 0) { lblMainLang.Text = "לא נבחרה שפה"; return; }
            foreach (var l in Catalog.Languages)
                if (l.Id == langOrder[0]) lblMainLang.Text = "שפה ראשית: " + l.Name + "\n(השפה הראשונה שסומנה)";
        }

        void LogLine(string s)
        {
            if (InvokeRequired) { BeginInvoke(new Action<string>(LogLine), s); return; }
            tbLog.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + s + "\r\n");
        }

        // read + validate the form; null when invalid
        Options ReadOptions(bool needKeyCheck)
        {
            var o = new Options();
            o.Product = (ProductDef)cbProduct.SelectedItem;
            o.Bit = rb32.Checked ? 32 : 64;
            o.Langs.AddRange(langOrder);
            for (int i = 0; i < clApps.Items.Count; i++)
                if (clApps.GetItemChecked(i)) o.Apps.Add(((Item)clApps.Items[i]).Id);
            o.Key = tbKey.Text.Trim();
            if (o.Langs.Count == 0) { Msg("בחר לפחות שפה אחת."); return null; }
            if (o.Apps.Count == 0) { Msg("בחר לפחות יישום אחד."); return null; }
            if (needKeyCheck && o.Key.Length > 0 && !ConfigBuilder.KeyRx.IsMatch(o.Key))
            { Msg("מפתח המוצר לא בפורמט XXXXX-XXXXX-XXXXX-XXXXX-XXXXX.\nאפשר להשאיר ריק ולהפעיל אחרי ההתקנה."); return null; }
            return o;
        }

        void Msg(string s) { MessageBox.Show(this, s, Text, MessageBoxButtons.OK, MessageBoxIcon.Information, MessageBoxDefaultButton.Button1, MessageBoxOptions.RtlReading | MessageBoxOptions.RightAlign); }

        bool Ask(string s)
        {
            return MessageBox.Show(this, s, Text, MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2,
                MessageBoxOptions.RtlReading | MessageBoxOptions.RightAlign) == DialogResult.Yes;
        }

        void Run(Action work)
        {
            foreach (var b in actions) b.Enabled = false;
            bar.Visible = true;
            var th = new Thread(delegate ()
            {
                try { work(); }
                catch (Exception ex) { LogLine("שגיאה: " + ex.Message); }
                finally
                {
                    BeginInvoke(new Action(delegate { foreach (var b in actions) b.Enabled = true; bar.Visible = false; }));
                }
            });
            th.IsBackground = true;
            th.Start();
        }

        // --- actions
        void DoRemove()
        {
            if (!Ask("כל גרסאות Office שזוהו יוסרו, כולל הרישיונות שלהן.\nשמור עבודה פתוחה וסגור את תוכנות Office.\n\nלהמשיך?")) return;
            Run(delegate { eng.Scrub("/A"); });
        }

        void DoInstall()
        {
            var o = ReadOptions(true); if (o == null) return;
            Run(delegate { eng.InstallOnline(o); });
        }

        void DoReinstall()
        {
            var o = ReadOptions(true); if (o == null) return;
            if (!Ask("שלב 1: כל גרסאות Office שזוהו יוסרו.\nשלב 2: " + o.Product.Name + " יותקן מהשרתים של Microsoft.\n\nלהמשיך?")) return;
            Run(delegate
            {
                eng.GetOdt();            // first, so nothing is removed if the download fails
                eng.Scrub("/A");
                eng.InstallOnline(o);
            });
        }

        void DoDownload()
        {
            var o = ReadOptions(false); if (o == null) return;
            Run(delegate { eng.Download(o); });
        }

        void DoOffline()
        {
            var dirs = new List<string>();
            if (Directory.Exists(eng.FilesDir))
                foreach (var d in Directory.GetDirectories(eng.FilesDir))
                    if (File.Exists(Path.Combine(d, "config.xml")) && Directory.Exists(Path.Combine(d, @"Office\Data"))) dirs.Add(d);

            string chosen = null;
            if (dirs.Count > 0) chosen = Pick("בחר חבילה שהורדה:", dirs);
            else
            {
                using (var fb = new FolderBrowserDialog { Description = "לא נמצאו הורדות ב-Office_Files. בחר תיקייה שמכילה config.xml ותיקיית Office" })
                    if (fb.ShowDialog(this) == DialogResult.OK) chosen = fb.SelectedPath;
            }
            if (chosen == null) return;
            if (!File.Exists(Path.Combine(chosen, "config.xml")) || !Directory.Exists(Path.Combine(chosen, @"Office\Data")))
            { Msg("בתיקייה הזו אין קבצי התקנה של Office."); return; }
            Run(delegate { eng.InstallOffline(chosen); });
        }

        string Pick(string title, List<string> dirs)
        {
            using (var f = new Form())
            {
                f.Text = title; f.RightToLeft = RightToLeft.Yes; f.RightToLeftLayout = true;
                f.ClientSize = new Size(520, 300); f.StartPosition = FormStartPosition.CenterParent;
                f.FormBorderStyle = FormBorderStyle.FixedDialog; f.MinimizeBox = f.MaximizeBox = false; f.Font = Font;
                var lb = new ListBox { Location = new Point(12, 12), Size = new Size(496, 230), RightToLeft = RightToLeft.No };
                foreach (var d in dirs) lb.Items.Add(Path.GetFileName(d));
                lb.SelectedIndex = 0;
                var ok = new Button { Text = "התקן", Location = new Point(12, 254), Size = new Size(120, 34), DialogResult = DialogResult.OK };
                var cancel = new Button { Text = "ביטול", Location = new Point(140, 254), Size = new Size(120, 34), DialogResult = DialogResult.Cancel };
                f.Controls.AddRange(new Control[] { lb, ok, cancel });
                f.AcceptButton = ok; f.CancelButton = cancel;
                return f.ShowDialog(this) == DialogResult.OK ? dirs[lb.SelectedIndex] : null;
            }
        }

        void DoLicense()
        {
            using (var f = new Form())
            {
                f.Text = "רישוי והפעלה"; f.RightToLeft = RightToLeft.Yes; f.RightToLeftLayout = true; f.Font = Font;
                f.ClientSize = new Size(460, 250); f.StartPosition = FormStartPosition.CenterParent;
                f.FormBorderStyle = FormBorderStyle.FixedDialog; f.MinimizeBox = f.MaximizeBox = false;
                f.Controls.Add(new Label { Text = "מפתח מוצר:", Location = new Point(12, 16), AutoSize = true });
                var tb = new TextBox { Location = new Point(12, 38), Size = new Size(436, 26), RightToLeft = RightToLeft.No, CharacterCasing = CharacterCasing.Upper, MaxLength = 29 };
                var b1 = new Button { Text = "הזן מפתח והפעל", Location = new Point(12, 76), Size = new Size(436, 36) };
                var b2 = new Button { Text = "הצג מצב רישוי", Location = new Point(12, 120), Size = new Size(436, 36) };
                var b3 = new Button { Text = "פתח Word להתחברות לחשבון Microsoft (Microsoft 365 / קמעונאי)", Location = new Point(12, 164), Size = new Size(436, 36) };
                f.Controls.AddRange(new Control[] { tb, b1, b2, b3 });
                b1.Click += delegate
                {
                    string k = tb.Text.Trim();
                    if (!ConfigBuilder.KeyRx.IsMatch(k)) { Msg("פורמט מפתח לא תקין."); return; }
                    f.Close();
                    Run(delegate { eng.Ospp("/inpkey:" + k); eng.Ospp("/act"); });
                };
                b2.Click += delegate { f.Close(); Run(delegate { eng.Ospp("/dstatus"); }); };
                b3.Click += delegate
                {
                    try { Process.Start("winword.exe"); f.Close(); }
                    catch { Msg("Word לא נמצא. ודא ש-Office מותקן."); }
                };
                if (Engine.OsppPath() == null) { b1.Enabled = b2.Enabled = false; tb.Enabled = false; }
                f.ShowDialog(this);
            }
        }

        void DoAdvanced()
        {
            using (var f = new Form())
            {
                f.Text = "הסרה מתקדמת"; f.RightToLeft = RightToLeft.Yes; f.RightToLeftLayout = true; f.Font = Font;
                f.ClientSize = new Size(460, 130); f.StartPosition = FormStartPosition.CenterParent;
                f.FormBorderStyle = FormBorderStyle.FixedDialog; f.MinimizeBox = f.MaximizeBox = false;
                var b1 = new Button { Text = "סריקה עמוקה (כולל גרסאות שלא זוהו - איטי)", Location = new Point(12, 16), Size = new Size(436, 44) };
                var b2 = new Button { Text = "פתח את התפריט המלא של כלי ההסרה", Location = new Point(12, 70), Size = new Size(436, 44) };
                f.Controls.AddRange(new Control[] { b1, b2 });
                b1.Click += delegate
                {
                    f.Close();
                    if (Ask("סריקה עמוקה תסיר את כל רכיבי Office ותיקח זמן רב.\nלהמשיך?")) Run(delegate { eng.Scrub("/A /Full"); });
                };
                b2.Click += delegate { f.Close(); Run(delegate { eng.Scrub(""); }); };
                f.ShowDialog(this);
            }
        }
    }

    // ------------------------------------------------------------------ entry
    static class Program
    {
        [STAThread]
        static void Main()
        {
            if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
            {
                try
                {
                    var psi = new ProcessStartInfo(Application.ExecutablePath) { UseShellExecute = true, Verb = "runas" };
                    Process.Start(psi);
                }
                catch
                {
                    MessageBox.Show("הכלי צריך הרשאות מנהל.", "Office Setup Tool", MessageBoxButtons.OK, MessageBoxIcon.Error,
                        MessageBoxDefaultButton.Button1, MessageBoxOptions.RtlReading | MessageBoxOptions.RightAlign);
                }
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }
}
