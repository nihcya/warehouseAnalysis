; WarehouseWorkbench 安装脚本（M3 Task 6，spec「安装包」需求）
;
; 编译方式（推荐由 scripts/build_release.py 驱动，版本号经 /DAppVersion 注入）：
;   ISCC /DAppVersion=x.y.z /O..\..\dist scripts\installer\warehouse-workbench.iss
;
; 版本号唯一来源为 apps/workbench-desktop/pyproject.toml 的 [project].version，
; 手工编译未传 /DAppVersion 时回落到下方默认值（升级发布前请确认一致）。
;
; 前置产物：需先运行 PyInstaller（workbench.spec），生成 dist\WarehouseWorkbench\
; 完整 --onedir 目录，本脚本将其整体安装到 Program Files。
;
; 语言：简体中文（需 Inno Setup 6.2+，官方自带 ChineseSimplified.isl）。
; 数据约定：用户数据在 %LOCALAPPDATA%\WarehouseWorkbench，卸载默认保留并提示手动清理。

#ifndef AppVersion
; 默认版本（与 apps/workbench-desktop/pyproject.toml 保持同步的回落值）
#define AppVersion "0.1.0"
#endif

#define AppName "仓库分析工作台"
#define AppExeName "WarehouseWorkbench.exe"
#define AppPublisher "仓库品类分析决策工具项目组"
#define AppIdGuid "{{5A2C8E14-9F3B-4E7D-B6A1-8C2D4E9F0B31}"

[Setup]
; AppId 固定不变：升级覆盖安装（含卸载信息）依赖同一 AppId 识别为同一程序
AppId={#AppIdGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
; 安装到 Program Files（64 位系统进 Program Files，需要管理员权限）
DefaultDirName={autopf}\WarehouseWorkbench
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 升级覆盖安装：检测到运行中的程序时提示关闭后再覆盖（文件占用保护）
CloseApplications=yes
; 仅 64 位 Windows（PyInstaller 产物按 64 位 Python 构建）
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; 产物输出（build_release.py 会用 /O 显式覆盖为仓库根 dist 目录）
OutputDir=..\..\dist
OutputBaseFilename=WarehouseWorkbench-Setup-{#AppVersion}
; 压缩与界面
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 卸载项显示名（控制面板“应用”列表）
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
; 官方简体中文语言文件（Inno Setup 6.2+ 随安装器附带）
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
; 桌面快捷方式（默认勾选；开始菜单快捷方式始终创建）
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; PyInstaller --onedir 产物整体复制（_internal 资源目录随 recursesubdirs 收入）
Source: "..\..\dist\WarehouseWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单快捷方式（含卸载入口）
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式（受 desktopicon 任务开关控制）
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // 卸载完成：按约定保留 %LOCALAPPDATA%\WarehouseWorkbench 用户数据
    // （数据库 / 备份 / 报告 / 登录凭据），仅提示路径供手动清理，绝不自动删除
    MsgBox(
      '卸载完成。' + #13#10 + #13#10 +
      '用户数据（数据库、备份、报告、登录凭据）已保留在：' + #13#10 +
      ExpandConstant('{localappdata}') + '\WarehouseWorkbench' + #13#10 + #13#10 +
      '如需彻底清理，请手动删除该目录。重新安装新版本时这些数据仍可继续使用。',
      mbInformation, MB_OK);
  end;
end;
