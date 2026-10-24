{ inputs, ... }:
{
  flake-file.inputs = {
    pyproject-nix.url = "github:pyproject-nix/pyproject.nix";
    pyproject-nix.inputs.nixpkgs.follows = "nixpkgs";
  };
  perSystem =
    { config, pkgs, ... }:
    let
      project = inputs.pyproject-nix.lib.project.loadPyproject {
        projectRoot = ../.;
      };

      pythonAttr = "python314";
      python = pkgs.${pythonAttr}.override {
        packageOverrides = _final: _prev: {
          inherit (config.packages) playsound3;
        };
      };

      runtimeTools = [ pkgs.unrar-free ];

      linkG3MTool = ''
        if [ -d src/assets ]; then
          mkdir -p src/assets/bin/g3mtool_linux
          ln -sfn ${config.packages.g3mtool}/bin/G3MTool src/assets/bin/g3mtool_linux/G3MTool
        fi
      '';
    in
    {
      _module.args.project = project;
      _module.args.python = python;

      devShells.default =
        let
          pythonEnv = python.withPackages (project.renderers.withPackages { inherit python; });
        in
        pkgs.mkShell {
          packages = [
            pythonEnv
            config.packages.g3mtool
          ]
          ++ runtimeTools;
          shellHook = linkG3MTool;
        };

      devShells.devtest =
        let
          testDeps = with python.pkgs; [
            pytest
            pytest-mock
            pytest-qt
            responses
          ];
          pythonEnv = python.withPackages (
            ps: (project.renderers.withPackages { inherit python; } ps) ++ testDeps
          );
        in
        pkgs.mkShell {
          packages = [
            pythonEnv
            config.packages.g3mtool
            python.pkgs.ruff
            pkgs.basedpyright
            pkgs.xvfb-run
            pkgs.openbox
          ]
          ++ runtimeTools;
          shellHook = linkG3MTool + ''
            export QT_QPA_PLATFORM=xcb
            export LC_ALL=C
            unset WAYLAND_DISPLAY
          '';
        };
    };
}
