; 급식 위젯 설치 프로그램 (Inno Setup 6)
; 관리자 권한 없이 사용자 폴더에 설치한다.
; 빌드: ISCC /DAppVersion=1.6.0 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7E2C5B1A-4D3F-4C86-9A57-5B1F3E6D2A10}
AppName=급식 위젯
AppVersion={#AppVersion}
AppVerName=급식 위젯 {#AppVersion}
DefaultDirName={localappdata}\Programs\LunchWidget
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=LunchWidget-Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\LunchWidget.exe
UninstallDisplayName=급식 위젯
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; widget.pyw의 SINGLE_INSTANCE_MUTEX와 같아야 한다. 실행 중이면 닫도록 안내한다.
AppMutex=LunchWidgetSingleInstance

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"
Name: "autostart"; Description: "Windows를 켤 때 자동으로 실행"

[Files]
Source: "..\dist\LunchWidget\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\급식 위젯"; Filename: "{app}\LunchWidget.exe"
Name: "{autodesktop}\급식 위젯"; Filename: "{app}\LunchWidget.exe"; Tasks: desktopicon

[Registry]
; 값 이름은 widget.pyw의 RUN_VALUE_NAME과 같아야 한다.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LunchWidget"; ValueData: """{app}\LunchWidget.exe"""; Tasks: autostart
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "LunchWidget"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\LunchWidget.exe"; Description: "급식 위젯 실행"; Flags: nowait postinstall skipifsilent
