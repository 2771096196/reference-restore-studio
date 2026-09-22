using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class PortableLauncher {
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        string root=AppDomain.CurrentDomain.BaseDirectory;
        try {
            Directory.CreateDirectory(Path.Combine(root,"logs"));
            string python=Path.Combine(root,"runtime","python.exe");
            string script=Path.Combine(root,"portable","manager.py");
            if (!File.Exists(python) || !File.Exists(script)) throw new Exception("请先完整解压程序包，再运行启动器。不要单独移动 EXE。");
            var info=new ProcessStartInfo(python,"-s \""+script+"\" "+(args.Length>0 && args[0]=="--check" ? "--check" : "--open"));
            info.WorkingDirectory=root;info.UseShellExecute=false;info.CreateNoWindow=true;info.WindowStyle=ProcessWindowStyle.Hidden;
            var p=Process.Start(info);
            if (args.Length>0 && args[0]=="--check") { p.WaitForExit(); return p.ExitCode; }
            if (p.WaitForExit(1800) && p.ExitCode!=0) throw new Exception("启动失败，请查看 logs\\manager.log。请将程序解压到可写目录。");
            return 0;
        } catch(Exception ex) { MessageBox.Show(ex.Message,"H3 便携工作台",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1; }
    }
}
