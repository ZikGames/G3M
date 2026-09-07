{
  perSystem =
    { pkgs, lib, ... }:
    {
      packages.g3mtool = pkgs.buildDotnetModule rec {
        pname = "g3mtool";
        version = "1.2.9";

        src = pkgs.fetchFromGitHub {
          owner = "y114git";
          repo = "G3MTool";
          tag = version;
          hash = "sha256-nRAudPcNsNR8tOmvs+zWiH1S6Sc9qn2tULvBnChBSqk=";
          # hash = lib.fakeHash;
        };

        projectFile = "G3MToolCLI/G3MToolCLI.csproj";
        nugetDeps = ./g3mtool-deps.json;

        dotnet-sdk = pkgs.dotnetCorePackages.sdk_10_0;
        dotnet-runtime = pkgs.dotnetCorePackages.runtime_10_0;

        selfContainedBuild = true;
        executables = [ "G3MTool" ];

        runtimeDeps = [
          pkgs.stdenv.cc.cc.lib
          pkgs.fontconfig
          pkgs.freetype
        ];

        meta = {
          description = "CLI tool and reference implementation for the .g3mpatch format";
          homepage = "https://github.com/y114git/G3MTool";
          license = lib.licenses.gpl3Plus;
          mainProgram = "G3MTool";
        };
      };
    };
}
