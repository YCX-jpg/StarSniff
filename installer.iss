; StarSniff 安装脚本（Inno Setup 6）
;
; 行为约定：
;   * 首次安装会显示「选择安装位置」页，默认装到 Program Files；
;   * 已安装过再运行，会自动定位到原目录（UsePreviousAppDir），
;     用户仍可在这个页面改到别的位置，等于「更新 or 换位置」；
;   * 卸载信息写进「应用和功能」，带图标和版本号。
;
; 编译：ISCC.exe installer.iss

#define MyAppName "StarSniff 星嗅"
#ifndef MyAppVersion
  #define MyAppVersion "2.1.7"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "dist"
#endif
#define MyAppPublisher "YCX"
#define MyAppURL "https://ycx700nas.online/"
#define MyAppExeName "StarSniff.exe"
#define MyAppId "{{9F2C7B14-3E61-4C1A-9D2B-7A5E4C8B1F30}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; 默认装到 Program Files；允许用户在「选择安装位置」里改
DefaultDirName={autopf}\StarSniff
DisableDirPage=no
; 已安装过就用上次的目录，但页面照常显示，用户可以改
UsePreviousAppDir=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 支持「仅为我安装」（免管理员）或「为所有用户安装」
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir={#MyOutputDir}
OutputBaseFilename=StarSniff-{#MyAppVersion}-setup
SetupIconFile=assets\starsniff.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; 升级时若程序还开着，先提示关闭
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
Source: "dist\StarSniff\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\Download"
