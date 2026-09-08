{
  flake = {
    nixosModules.default =
      {
        config,
        lib,
        pkgs,
        ...
      }:
      let
        cfg = config.programs.g3m;
      in
      {
        options.programs.g3m = {
          enable = lib.mkEnableOption "G3M — GameMaker Mod Manager";

          package = lib.mkOption {
            type = lib.types.package;
            description = ''
              The G3M package to install. Point this at this flake's
              `packages.<system>.g3m` (e.g. via `inputs.g3m.packages.${pkgs.system}.g3m`).
            '';
          };
        };

        config = lib.mkIf cfg.enable {
          environment.systemPackages = [ cfg.package ];

          # register xdgmime to avoid single line in the log (but it doesnt work for me because i donothave it or something)
          xdg.mime.defaultApplications."x-scheme-handler/g3m" = "g3m.desktop";
          xdg.mime.defaultApplications."x-scheme-handler/deltahub" = "g3m.desktop";
        };
      };
    homeModules.default =
      {
        config,
        lib,
        pkgs,
        ...
      }:
      let
        cfg = config.programs.g3m;

        declared = lib.filterAttrs (_: v: v != null) {
          language = cfg.language;
          game_path = cfg.gamePath;
          undertale_game_path = cfg.undertaleGamePath;
          pizzatower_game_path = cfg.pizzatowerGamePath;
          frickbears3_game_path = cfg.frickbears3GamePath;
          launch_via_steam = cfg.launchViaSteam;
          use_portproton = cfg.usePortproton;
          portproton_path = cfg.portprotonPath;
          mods_per_page = cfg.modsPerPage;
          disable_discord_rich_presence = cfg.disableDiscordRichPresence;
          active_theme_name = cfg.themeName;
          custom_g3mtool_path = cfg.customG3mtoolPath;
          disable_splash = cfg.disableSplash;
          skip_patching_warnings = cfg.skipPatchingWarnings;
        };

        declaredJson = pkgs.writeText "g3m-declared-settings.json" (builtins.toJSON declared);
      in
      {
        options.programs.g3m = {
          enable = lib.mkEnableOption "declarative G3M settings";

          language = lib.mkOption {
            type = lib.types.nullOr (
              lib.types.enum [
                "en"
                "ru"
              ]
            );
            default = null;
          };

          gamePath = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
            description = "Path to the DELTARUNE installation.";
          };

          undertaleGamePath = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
          };

          pizzatowerGamePath = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
          };

          frickbears3GamePath = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
          };

          launchViaSteam = lib.mkOption {
            type = lib.types.nullOr lib.types.bool;
            default = null;
          };

          usePortproton = lib.mkOption {
            type = lib.types.nullOr lib.types.bool;
            default = null;
          };

          portprotonPath = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
          };

          modsPerPage = lib.mkOption {
            type = lib.types.nullOr lib.types.int;
            default = null;
          };

          disableDiscordRichPresence = lib.mkOption {
            type = lib.types.nullOr lib.types.bool;
            default = null;
          };

          themeName = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
          };

          customG3mtoolPath = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
          };

          disableSplash = lib.mkOption {
            type = lib.types.nullOr lib.types.bool;
            default = null;
          };

          skipPatchingWarnings = lib.mkOption {
            type = lib.types.nullOr lib.types.bool;
            default = null;
          };
        };

        config = lib.mkIf cfg.enable {
          # merge only that things that is needed
          home.activation.g3mSettings = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
            settingsFile="$HOME/.local/share/G3M/settings/settings.json"
            mkdir -p "$(dirname "$settingsFile")"
            if [ -f "$settingsFile" ]; then
              $DRY_RUN_CMD ${lib.getExe pkgs.jq} -s '.[0] * .[1]' "$settingsFile" ${declaredJson} > "$settingsFile.tmp"
              $DRY_RUN_CMD mv "$settingsFile.tmp" "$settingsFile"
            else
              $DRY_RUN_CMD cp ${declaredJson} "$settingsFile"
            fi
          '';
        };
      };
  };
}
