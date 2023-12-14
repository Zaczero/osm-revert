{ pkgsnix ? import ./pkgs.nix
, pkgs ? pkgsnix.pkgs
, unstable ? pkgsnix.unstable
}:

with pkgs; let
  shell = import ./shell.nix {
    inherit pkgs;
    inherit unstable;
    isDocker = true;
  };

  python-venv = buildEnv {
    name = "python-venv";
    paths = [
      (runCommand "python-venv" { } ''
        mkdir -p $out/lib
        cp -r "${./.venv/lib/python3.11/site-packages}"/* $out/lib
      '')
    ];
  };
in
dockerTools.buildLayeredImage {
  name = "zaczero/osm-revert";
  tag = "latest";
  maxLayers = 10;

  contents = shell.buildInputs ++ [ python-venv ];

  extraCommands = ''
    set -e
    mkdir app && cd app
    cp "${./.}"/LICENSE .
    cp "${./.}"/Makefile .
    cp "${./.}"/*.py .
    ${shell.shellHook}
  '';

  config = {
    WorkingDir = "/app";
    Env = [
      "PYTHONPATH=${python-venv}/lib"
      "PYTHONUNBUFFERED=1"
      "PYTHONDONTWRITEBYTECODE=1"
      "OSM_REVERT_VERSION_SUFFIX=docker"
    ];
    Entrypoint = [ "python" "main.py" ];
    Cmd = [ ];
  };
}
