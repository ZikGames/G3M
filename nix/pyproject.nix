{
  flake-file.inputs = {
    inputs.pyproject-nix.url = "github:pyproject-nix/pyproject.nix";
    inputs.pyproject-nix.inputs.nixpkgs.follows = "nixpkgs";
  };
  perSystem =
    { nixpkgs, pyproject-nix, ... }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      project = pyproject-nix.lib.project.loadPyproject {
        projectRoot = ./.;
      };

      pythonAttr = "python314";
    in
    {
      devShells = forAllSystems (system: {
        default =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            python = pkgs.${pythonAttr};
            pythonEnv = python.withPackages (project.renderers.withPackages { inherit python; });
          in
          pkgs.mkShell { packages = [ pythonEnv ]; };
      });

      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.${pythonAttr};
        in
        {
          default = python.pkgs.buildPythonPackage (project.renderers.buildPythonPackage { inherit python; });
        }
      );
    };
}
