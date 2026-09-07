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
        };

      devShells.test =
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
            pkgs.xvfb-run
            pkgs.openbox
          ]
          ++ runtimeTools;
        };
    };
}
