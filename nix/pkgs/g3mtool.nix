{
  perSystem =
    { pkgs, lib, ... }:let
    version = "1.3.0";

    src = pkgs.fetchFromGitHub {
      owner = "y114git";
      repo = "G3MTool";
      tag = "2026.09.12";
      hash = "sha256-BCxPrsIHQIh1HctGf/lH57wC82SkQkNJdvzZXLi3AAw=";
      # hash = lib.fakeHash;
    };

    dotnet-sdk = pkgs.dotnetCorePackages.sdk_10_0;
    dotnet-runtime = pkgs.dotnetCorePackages.runtime_10_0;
    in
    {
      packages.g3mtool = pkgs.buildDotnetModule rec {
        pname = "g3mtool";
        inherit version src dotnet-sdk dotnet-runtime;

        projectFile = "G3MToolCLI/G3MToolCLI.csproj";
        nugetDeps = ./g3mtool-deps.json;

        selfContainedBuild = true;
        executables = [ "G3MTool" ];

        meta = {
          description = "CLI tool and reference implementation for the .g3mpatch format";
          homepage = "https://github.com/y114git/G3MTool";
          license = lib.licenses.gpl3Plus;
          mainProgram = "G3MTool";
        };
      };
      packages.g3mtool-gui = pkgs.buildDotnetModule {
        pname = "g3mtool-gui";
        inherit version src dotnet-sdk dotnet-runtime;

        projectFile = "G3MToolGUI/G3MToolGUI.csproj";
        nugetDeps = ./g3mtool-gui-deps.json;

        selfContainedBuild = true;
        executables = [ "G3MToolGUI" ];
        runtimeDeps = [
          pkgs.stdenv.cc.cc.lib
          pkgs.fontconfig
          pkgs.freetype
          pkgs.libX11
          pkgs.libICE
          pkgs.libSM
        ];

        meta = {
          description = "GUI for G3MTool — reference implementation for the .g3mpatch format";
          homepage = "https://github.com/y114git/G3MTool";
          license = lib.licenses.gpl3Plus;
          mainProgram = "G3MToolGUI";
        };
      };
    };
}
