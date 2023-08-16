{ pkgs ? import <nixpkgs> { } }:

with pkgs; let
  shell = import ./shell.nix {
    inherit pkgs;
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
  name = "docker.monicz.pl/osm-revert-ui";
  tag = "latest";
  maxLayers = 10;

  contents = shell.buildInputs ++ [ python-venv ];

  extraCommands = ''
    set -e
    mkdir app && cd app
    cp "${./.}"/LICENSE .
    cp "${./.}"/Makefile .
    cp "${./.}"/*.py .
    cp -r "${./.}"/static .
    cp -r "${./.}"/templates .
    ${shell.shellHook}
  '';

  config = {
    WorkingDir = "/app";
    Env = [
      "LD_LIBRARY_PATH=${lib.makeLibraryPath shell.buildInputs}"
      "PYTHONPATH=${python-venv}/lib"
      "PYTHONUNBUFFERED=1"
      "PYTHONDONTWRITEBYTECODE=1"
      "OSM_REVERT_VERSION_SUFFIX=docker-ui"
    ];
    Entrypoint = [ "python" "-m" "uvicorn" "main:app" ];
    Cmd = [ "--host" "0.0.0.0" ];
  };
}
