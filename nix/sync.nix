{
  perSystem =
    {
      config,
      pkgs,
      lib,
      ...
    }:
    {
      packages.sync = pkgs.writeShellApplication {
        name = "g3m-sync";
        runtimeInputs = with pkgs; [
          git
          ruff
          gnugrep
          coreutils
        ];
        text = builtins.readFile ./g3m-sync.sh;
      };

      apps.sync = {
        type = "app";
        program = lib.getExe config.packages.sync;
        meta.description = "Safely sync python-nix with the original G3M";
      };
    };
}
