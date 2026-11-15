using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("AutoClip AI Studio")]
[assembly: AssemblyDescription("Standalone Desktop AI Viral Shorts & Studio Suite")]
[assembly: AssemblyConfiguration("")]
[assembly: AssemblyCompany("AutoClip Technologies")]
[assembly: AssemblyProduct("AutoClip AI Studio")]
[assembly: AssemblyCopyright("Copyright © 2026 AutoClip")]
[assembly: AssemblyTrademark("")]
[assembly: AssemblyCulture("")]
[assembly: ComVisible(false)]
[assembly: Guid("e5f3b7c8-9d2a-4f1e-8a3b-7c8d9e2a4f1e")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace AutoClip
{
    static class Program
    {
        private static Process _backendProcess;
        private static NotifyIcon _trayIcon;
        private static string _localIp = "127.0.0.1";
        private static string _mobileUrl = "http://127.0.0.1:8765";

        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            // Single instance mutex
            bool isNewInstance;
            using (Mutex mutex = new Mutex(true, "AutoClipStudio_SingleInstanceMutex", out isNewInstance))
            {
                if (!isNewInstance)
                {
                    // Already running - just open/focus the browser window
                    OpenStudioWindow("http://localhost:8765");
                    return;
                }

                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                Directory.SetCurrentDirectory(baseDir);

                // Detect local Wi-Fi IP for phone connectivity
                _localIp = GetLocalIpAddress();
                _mobileUrl = string.Format("http://{0}:8765", _localIp);

                // Locate Python in .venv or system
                string pythonExe = FindPythonExecutable(baseDir);
                if (string.IsNullOrEmpty(pythonExe) || !File.Exists(pythonExe))
                {
                    MessageBox.Show(
                        "AutoClip could not find the Python environment at:\n" +
                        Path.Combine(baseDir, ".venv", "Scripts", "python.exe") + "\n\n" +
                        "Please verify the environment or run setup.",
                        "AutoClip AI - Error",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }

                // Check if backend is already listening on 8765
                if (!IsBackendHealthy("http://127.0.0.1:8765/api/health"))
                {
                    StartBackendProcess(pythonExe, baseDir);
                    WaitForBackendReady("http://127.0.0.1:8765/api/health", 25);
                }

                // Setup System Tray Icon
                SetupTrayIcon(baseDir);

                // Open Standalone Desktop App Window
                OpenStudioWindow("http://localhost:8765");

                // Show notification tip
                try
                {
                    _trayIcon.ShowBalloonTip(
                        4000,
                        "AutoClip AI Studio Ready",
                        string.Format("PC: http://localhost:8765\nMobile: {0}", _mobileUrl),
                        ToolTipIcon.Info
                    );
                }
                catch {}

                // Application lifecycle loop
                Application.Run();

                // Clean shutdown on exit
                ShutdownBackend();
            }
        }

        private static string FindPythonExecutable(string baseDir)
        {
            string venvPy = Path.Combine(baseDir, ".venv", "Scripts", "python.exe");
            if (File.Exists(venvPy)) return venvPy;

            string venvPyw = Path.Combine(baseDir, ".venv", "Scripts", "pythonw.exe");
            if (File.Exists(venvPyw)) return venvPyw;

            string localPy = Path.Combine(baseDir, "python.exe");
            if (File.Exists(localPy)) return localPy;

            return venvPy;
        }

        private static void LogMessage(string baseDir, string msg)
        {
            try
            {
                string logDir = Path.Combine(baseDir, "data");
                if (!Directory.Exists(logDir)) Directory.CreateDirectory(logDir);
                File.AppendAllText(Path.Combine(logDir, "launcher.log"), string.Format("[{0:yyyy-MM-dd HH:mm:ss}] {1}\r\n", DateTime.Now, msg));
            }
            catch {}
        }

        private static void StartBackendProcess(string pythonExe, string baseDir)
        {
            try
            {
                LogMessage(baseDir, string.Format("Starting Python backend: {0}", pythonExe));
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = pythonExe;
                psi.Arguments = "-m uvicorn autoclip_app.app:app --host 0.0.0.0 --port 8765";
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;

                _backendProcess = Process.Start(psi);
                LogMessage(baseDir, string.Format("Python process started with PID: {0}", _backendProcess != null ? _backendProcess.Id : 0));
            }
            catch (Exception ex)
            {
                LogMessage(baseDir, string.Format("Failed to start backend: {0}", ex));
                MessageBox.Show("Failed to launch AutoClip backend:\n" + ex.Message, "AutoClip AI", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private static bool IsBackendHealthy(string url)
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Timeout = 1500;
                req.Method = "GET";
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                {
                    return resp.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        private static void WaitForBackendReady(string url, int timeoutSeconds)
        {
            int elapsed = 0;
            while (elapsed < timeoutSeconds * 1000)
            {
                if (IsBackendHealthy(url))
                {
                    LogMessage(AppDomain.CurrentDomain.BaseDirectory, string.Format("Backend is ready and healthy at {0} (elapsed: {1}ms)", url, elapsed));
                    return;
                }
                Thread.Sleep(400);
                elapsed += 400;
            }
            LogMessage(AppDomain.CurrentDomain.BaseDirectory, string.Format("Backend did not report ready after {0} seconds", timeoutSeconds));
        }

        private static void OpenStudioWindow(string url)
        {
            // Look for Microsoft Edge to launch in Native Application Mode (--app=)
            string edge86 = @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe";
            string edge64 = @"C:\Program Files\Microsoft\Edge\Application\msedge.exe";
            string edgePath = File.Exists(edge86) ? edge86 : (File.Exists(edge64) ? edge64 : null);

            if (edgePath != null)
            {
                try
                {
                    string profileDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data", "desktop_profile");
                    ProcessStartInfo psi = new ProcessStartInfo();
                    psi.FileName = edgePath;
                    psi.Arguments = string.Format("--app=\"{0}\" --window-size=1300,880 --user-data-dir=\"{1}\"", url, profileDir);
                    Process.Start(psi);
                    return;
                }
                catch {}
            }

            // Fallback to default browser
            try
            {
                Process.Start(url);
            }
            catch
            {
                try
                {
                    Process.Start("cmd.exe", "/c start " + url);
                }
                catch {}
            }
        }

        private static string GetLocalIpAddress()
        {
            try
            {
                foreach (NetworkInterface ni in NetworkInterface.GetAllNetworkInterfaces())
                {
                    if (ni.OperationalStatus != OperationalStatus.Up) continue;
                    if (ni.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;

                    IPInterfaceProperties ipProps = ni.GetIPProperties();
                    foreach (UnicastIPAddressInformation addr in ipProps.UnicastAddresses)
                    {
                        if (addr.Address.AddressFamily == AddressFamily.InterNetwork)
                        {
                            string s = addr.Address.ToString();
                            if (!s.StartsWith("127.") && !s.StartsWith("169.254."))
                            {
                                return s;
                            }
                        }
                    }
                }
            }
            catch {}
            return "127.0.0.1";
        }

        private static void SetupTrayIcon(string baseDir)
        {
            _trayIcon = new NotifyIcon();
            _trayIcon.Text = "AutoClip AI - Autonomous Studio";

            string icoPath = Path.Combine(baseDir, "AutoClip.ico");
            if (File.Exists(icoPath))
            {
                try { _trayIcon.Icon = new Icon(icoPath); }
                catch { _trayIcon.Icon = SystemIcons.Application; }
            }
            else
            {
                _trayIcon.Icon = SystemIcons.Application;
            }

            ContextMenu menu = new ContextMenu();

            MenuItem openItem = new MenuItem("🚀 Open AutoClip Studio", delegate { OpenStudioWindow("http://localhost:8765"); });
            openItem.DefaultItem = true;
            menu.MenuItems.Add(openItem);

            menu.MenuItems.Add("📱 Copy Mobile Wi-Fi URL (" + _localIp + ":8765)", delegate {
                try
                {
                    Clipboard.SetText(_mobileUrl);
                    _trayIcon.ShowBalloonTip(2000, "AutoClip Mobile", "Copied: " + _mobileUrl, ToolTipIcon.Info);
                }
                catch {}
            });

            menu.MenuItems.Add("📥 Download Android APK", delegate {
                OpenStudioWindow("http://localhost:8765/api/download-apk");
            });

            menu.MenuItems.Add("-");

            menu.MenuItems.Add("❌ Exit AutoClip Studio", delegate {
                ShutdownBackend();
                if (_trayIcon != null)
                {
                    _trayIcon.Visible = false;
                    _trayIcon.Dispose();
                }
                Application.Exit();
            });

            _trayIcon.ContextMenu = menu;
            _trayIcon.DoubleClick += delegate { OpenStudioWindow("http://localhost:8765"); };
            _trayIcon.Visible = true;
        }

        private static void ShutdownBackend()
        {
            if (_backendProcess != null && !_backendProcess.HasExited)
            {
                try
                {
                    ProcessStartInfo killPsi = new ProcessStartInfo("taskkill", string.Format("/PID {0} /T /F", _backendProcess.Id));
                    killPsi.CreateNoWindow = true;
                    killPsi.UseShellExecute = false;
                    Process p = Process.Start(killPsi);
                    if (p != null) p.WaitForExit(3000);
                }
                catch {}

                try
                {
                    if (!_backendProcess.HasExited) _backendProcess.Kill();
                }
                catch {}
            }
        }
    }
}
