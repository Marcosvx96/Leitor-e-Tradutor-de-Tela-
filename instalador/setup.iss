; Inno Setup 6 - instalador do Leitor em Voz Alta
; Gerado por gerar_instalador.py. Não editar caminhos manualmente.

#define AppName "Leitor em Voz Alta"
#define AppVersion "1.0"
#define AppExeName "LeitorVozAlta.exe"

[Setup]
AppId={{A7C4E2B1-6D90-4F3E-9C12-8B5D7E0F4A33}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=LeitorVozAlta-Setup
SetupIconFile=..\icone.ico
UninstallDisplayIcon={app}\icone.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
AllowNoIcons=yes
CloseApplications=yes
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "pt_BR"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked

[Files]
Source: "..\dist\LeitorVozAlta\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icone.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icone.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Abrir o {#AppName} agora"; Flags: nowait postinstall skipifsilent
