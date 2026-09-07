{ inputs, ... }:
{
  imports = [
    inputs.flake-parts.flakeModules.easyOverlay
    inputs.flake-file.flakeModules.dendritic
  ];
  flake-file = { lib, ... }: {
    outputs = lib.mkForce ''
      inputs: inputs.flake-parts.lib.mkFlake { inherit inputs; } (inputs.import-tree ./nix)
    '';
  };
}
