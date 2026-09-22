// Build with a release-specific manifest substituted for __RELEASE_MANIFEST__.
// Downloads/extracts files only; no registry, system PATH, installer or admin changes.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public class Part { public string Name; public long Size; public string Sha256; }
public class ReleaseInfo { public string Version; public string BaseUrl; public string ArchiveSha256; public Part[] Parts; }

static class PackageDownload {
    public const string ManifestJson = @"__RELEASE_MANIFEST__";
    public static readonly ReleaseInfo Info = new JavaScriptSerializer().Deserialize<ReleaseInfo>(ManifestJson);
    public static volatile bool Cancel;
    public static HttpWebRequest ActiveRequest;
    public static string Hash(string path) {
        using (var hash=SHA256.Create()) using(var input=File.OpenRead(path))
            return BitConverter.ToString(hash.ComputeHash(input)).Replace("-","").ToLowerInvariant();
    }
    static void CheckCancel() { if(Cancel) throw new OperationCanceledException("已暂停，下载文件保留，可再次继续。"); }
    public static string Install(string cache,string destination,bool online,Action<string,int> progress) {
        Directory.CreateDirectory(cache);Directory.CreateDirectory(destination);
        string result=Path.Combine(destination,"H3-Portable");
        string marker=Path.Combine(result,".h3-portable-installing");
        if(Directory.Exists(result) && Directory.GetFileSystemEntries(result).Length>0 &&
                (!File.Exists(marker) || File.ReadAllText(marker)!=Info.ArchiveSha256))
            throw new Exception("目标目录已有 H3-Portable。请选择其他解压位置，以免覆盖原文件。");
        ServicePointManager.SecurityProtocol=SecurityProtocolType.Tls12;
        foreach(var part in Info.Parts) {
            CheckCancel();string path=Path.Combine(cache,part.Name);
            if(File.Exists(path) && new FileInfo(path).Length==part.Size && Hash(path)==part.Sha256) continue;
            if(!online) throw new Exception("缺少或校验失败："+part.Name);
            long offset=File.Exists(path)?new FileInfo(path).Length:0;
            if(offset>=part.Size) { File.Move(path,path+".invalid-"+DateTime.UtcNow.Ticks);offset=0; }
            var request=(HttpWebRequest)WebRequest.Create(Info.BaseUrl+Uri.EscapeDataString(part.Name));
            ActiveRequest=request;request.UserAgent="H3-Portable-Downloader/1.0";request.Timeout=30000;request.ReadWriteTimeout=30000;
            if(offset>0)request.AddRange(offset);
            using(var response=(HttpWebResponse)request.GetResponse()) {
                if(response.ResponseUri.Scheme!="https")throw new Exception("下载重定向不是 HTTPS。");
                bool resume=offset>0 && response.StatusCode==HttpStatusCode.PartialContent && (response.Headers["Content-Range"]??"").StartsWith("bytes "+offset+"-");
                if(!resume)offset=0;
                using(var input=response.GetResponseStream()) using(var output=new FileStream(path,resume?FileMode.Append:FileMode.Create,FileAccess.Write,FileShare.Read)) {
                    byte[] buffer=new byte[1024*1024];int read;
                    while((read=input.Read(buffer,0,buffer.Length))>0) {
                        CheckCancel();output.Write(buffer,0,read);offset+=read;
                        if(offset>part.Size)throw new Exception("下载文件大于预期大小。");
                        progress("下载 "+part.Name+" · "+(offset/1048576)+" / "+(part.Size/1048576)+" MiB",(int)(100*offset/part.Size));
                    }
                }
            }
            ActiveRequest=null;progress("校验 "+part.Name,100);
            if(new FileInfo(path).Length!=part.Size || Hash(path)!=part.Sha256)
                throw new Exception("文件尚未完整或校验失败："+part.Name+"。再次点击可重试。");
        }
        CheckCancel();string archive;
        if(Info.Parts.Length==1)archive=Path.Combine(cache,Info.Parts[0].Name);
        else {
            archive=Path.Combine(cache,"H3-Portable-combined.zip");
            progress("合并下载分卷…",0);
            using(var output=File.Create(archive)) foreach(var part in Info.Parts) {
                CheckCancel();using(var input=File.OpenRead(Path.Combine(cache,part.Name)))input.CopyTo(output);
            }
        }
        progress("校验完整程序包…",0);
        if(Hash(archive)!=Info.ArchiveSha256)throw new Exception("完整包校验失败，请重新下载。");
        // Extract directly into an owned, marked target. Windows scanners may
        // temporarily lock new DLLs against renaming an entire staging folder.
        // The marker permits safe retry only for this exact unfinished archive.
        Directory.CreateDirectory(result);File.WriteAllText(marker,Info.ArchiveSha256);
        string boundary=Path.GetFullPath(result).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
        using(var zip=ZipFile.OpenRead(archive)) {
            long total=0,done=0;foreach(var entry in zip.Entries)total+=entry.Length;
            var drive=new DriveInfo(Path.GetPathRoot(Path.GetFullPath(destination)));
            if(drive.AvailableFreeSpace<total+128*1048576L)throw new Exception("解压位置空间不足，至少需要 "+Math.Ceiling(total/1073741824.0)+" GiB。");
            foreach(var entry in zip.Entries) {
                CheckCancel();string target=Path.GetFullPath(Path.Combine(destination,entry.FullName.Replace('/',Path.DirectorySeparatorChar)));
                if(!target.StartsWith(boundary,StringComparison.OrdinalIgnoreCase) && target.TrimEnd(Path.DirectorySeparatorChar)!=result)throw new Exception("压缩包路径不安全。");
                if(String.IsNullOrEmpty(entry.Name)){Directory.CreateDirectory(target);continue;}
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using(var input=entry.Open())using(var output=File.Create(target))input.CopyTo(output);
                done+=entry.Length;progress("解压程序文件 · "+(done/1048576)+" / "+(total/1048576)+" MiB",total>0?(int)(100*done/total):100);
            }
        }
        File.Delete(marker);
        progress("已准备好，点击打开工作台。",100);
        return result;
    }
    [STAThread] public static int Main(string[] args) {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling",false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths",false);
        if(args.Length==3 && args[0]=="--extract-local") {
            try { Install(Path.GetFullPath(args[1]),Path.GetFullPath(args[2]),false,(s,p)=>{});return 0; }
            catch(Exception ex){File.WriteAllText(Path.Combine(args[1],"extract-test.log"),ex.ToString());return 1;}
        }
        Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new DownloadForm());return 0;
    }
}

class DownloadForm:Form {
    TextBox path=new TextBox();Label status=new Label();ProgressBar progress=new ProgressBar();Button start=new Button(),cancel=new Button(),open=new Button();bool busy;string installed;
    public DownloadForm() {
        Text="H3 便携包下载与解压";ClientSize=new Size(610,335);StartPosition=FormStartPosition.CenterScreen;Font=new Font("Microsoft YaHei UI",10);FormBorderStyle=FormBorderStyle.FixedSingle;MaximizeBox=false;
        Controls.Add(new Label{Text="H3 + 原图回贴 · 便携整合包",Font=new Font(Font.FontFamily,17,FontStyle.Bold),AutoSize=true,Location=new Point(22,20)});
        Controls.Add(new Label{Text="自动下载、校验并解压，不安装系统环境。请选择解压位置。",AutoSize=true,Location=new Point(24,66)});
        path.SetBounds(24,102,452,28);path.Text=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),"Downloads");Controls.Add(path);
        Button choose=new Button{Text="选择位置",Location=new Point(488,99),Size=new Size(98,32)};choose.Click+=(s,e)=>{using(var d=new FolderBrowserDialog()){if(d.ShowDialog(this)==DialogResult.OK)path.Text=d.SelectedPath;}};Controls.Add(choose);
        status.SetBounds(24,148,562,50);status.Text="需要下载程序包并预留约12 GiB空间；大型H3模型在工作台中另行选择。";Controls.Add(status);
        progress.SetBounds(24,206,562,18);Controls.Add(progress);
        start.Text="下载并解压";start.SetBounds(24,250,155,38);start.Click+=(s,e)=>Begin();Controls.Add(start);
        cancel.Text="暂停";cancel.SetBounds(190,250,90,38);cancel.Enabled=false;cancel.Click+=(s,e)=>{PackageDownload.Cancel=true;if(PackageDownload.ActiveRequest!=null)PackageDownload.ActiveRequest.Abort();};Controls.Add(cancel);
        open.Text="打开工作台";open.SetBounds(423,250,163,38);open.Enabled=false;open.Click+=(s,e)=>Process.Start(new ProcessStartInfo(Path.Combine(installed,"H3便携启动器.exe")){UseShellExecute=true});Controls.Add(open);
        FormClosing+=(s,e)=>{if(busy){e.Cancel=true;status.Text="请先暂停下载，等待当前文件操作结束。";}};
    }
    void Begin() {
        if(busy)return;string destination=Path.GetFullPath(path.Text);string cache=Path.Combine(destination,"H3-Portable-downloads");busy=true;start.Enabled=false;cancel.Enabled=true;path.Enabled=false;PackageDownload.Cancel=false;
        Task.Factory.StartNew(()=>{
            try {
                long lastUpdate=0;
                installed=PackageDownload.Install(cache,destination,true,(message,value)=>{long now=DateTime.UtcNow.Ticks;if(value<100 && now-lastUpdate<1000000)return;lastUpdate=now;BeginInvoke((Action)(()=>{status.Text=message;progress.Value=Math.Max(0,Math.Min(100,value));}));});
                BeginInvoke((Action)(()=>open.Enabled=true));
            } catch(Exception ex){BeginInvoke((Action)(()=>status.Text=PackageDownload.Cancel?"已暂停。再次点击可继续下载或重新完成解压。":ex.Message));}
            finally{BeginInvoke((Action)(()=>{busy=false;start.Enabled=true;cancel.Enabled=false;path.Enabled=true;}));}
        });
    }
}
