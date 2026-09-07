{
  perSystem =
    {
      config,
      pkgs,
      lib,
      project,
      python,
      ...
    }:
    let
      buildAttrs = project.renderers.buildPythonPackage { inherit python; };
      pythonDeps = buildAttrs.dependencies or buildAttrs.propagatedBuildInputs or [ ];

      runtimePath = lib.makeBinPath [
        pkgs.ffmpeg-full
        pkgs.alsa-utils
        pkgs.gst_all_1.gstreamer
        pkgs.gst_all_1.gst-plugins-base
        pkgs.unrar-free
      ];
    in
    {
      packages.g3m = python.pkgs.buildPythonApplication (
        buildAttrs
        // {
          # версии в nixpkgs не совпадают строго с пинами из pyproject.toml —
          # иначе pythonRuntimeDepsCheckHook валит сборку
          pythonRelaxDeps = [
            "PyQt6"
            "defusedxml"
            "rarfile"
          ];

          postInstall = ''
            # gettin content
            cp -a src/. "$out/${python.sitePackages}/"

            install -Dm755 ${config.packages.g3mtool}/bin/G3MTool \
              "$out/${python.sitePackages}/assets/bin/g3mtool_linux/G3MTool"

            install -Dm644 src/assets/images/logo.png \
              "$out/share/pixmaps/g3m.png"

            makeWrapper ${python.interpreter} "$out/bin/g3m" \
              --add-flags "$out/${python.sitePackages}/main.py" \
              --set PYTHONPATH "$out/${python.sitePackages}:${python.pkgs.makePythonPath pythonDeps}" \
              --set DRP_CLIENT_ID "1546602200313102336" \
              --prefix PATH : ${runtimePath}
          '';

          nativeBuildInputs = (buildAttrs.nativeBuildInputs or [ ]) ++ [
            pkgs.makeWrapper
            pkgs.copyDesktopItems
            pkgs.qt6.wrapQtAppsHook
            pkgs.qt6.qtwayland
          ];

          desktopItems = [
            (pkgs.makeDesktopItem {
              name = "g3m";
              exec = "g3m";
              icon = "g3m";
              desktopName = "G3M";
              genericName = "GameMaker Mod Manager";
              comment = "Desktop mod manager for GameMaker games";
              categories = [
                "Game"
                "Utility"
              ];
              mimeTypes = [
                "x-scheme-handler/g3m"
                "x-scheme-handler/deltahub"
              ];
            })
          ];

          doCheck = false;

          meta = (buildAttrs.meta or { }) // {
            description = "Mod Manager for GameMaker games";
            homepage = "https://github.com/ZikGames/G3M";
            license = lib.licenses.gpl3Plus;
            mainProgram = "g3m";
          };
        }
      );

      packages.default = config.packages.g3m;

      apps.default = {
        type = "app";
        program = "${config.packages.g3m}/bin/g3m";
        meta = { inherit (config.packages.g3m.meta) description; };
      };

      checks.pytest =
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
        pkgs.stdenv.mkDerivation {
          pname = "g3m-pytest";
          version = buildAttrs.version;
          src = ../.;

          nativeBuildInputs = [
            pythonEnv
            pkgs.xvfb-run
            pkgs.openbox
          ];

          dontConfigure = true;
          dontBuild = true;
          doCheck = true;

          checkPhase = ''
            runHook preCheck
            export HOME=$(mktemp -d)
            export QT_QPA_PLATFORM=xcb

            xvfb-run -a --server-args="-screen 0 1920x1080x24" bash -c '
              openbox --sm-disable &
              sleep 1
              pytest -k "not test_removed_usage_reporting_has_no_tracked_references and not test_startup_with_sample_archive and not test_disable_plugin_actions_hides_plugin_section"
            '

            runHook postCheck
          '';

          installPhase = "touch $out";
        };
    };
}
