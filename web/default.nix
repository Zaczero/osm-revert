{ pkgs ? import <nixpkgs> { }, ... }:

let
  shell = import ./shell.nix { isDevelopment = false; };
  python-venv = pkgs.buildEnv {
    name = "python-venv";
    paths = [
      (pkgs.runCommand "python-venv" { } ''
        mkdir -p $out/lib
        cp -r "${./.venv/lib/python3.12/site-packages}"/* $out/lib
      '')
    ];
  };
in
with pkgs; dockerTools.buildLayeredImage {
  name = "docker.monicz.dev/osm-revert-ui";
  tag = "latest";

  contents = shell.buildInputs ++ [ python-venv ];

  extraCommands = ''
    set -e
    mkdir app && cd app
    cp "${./.}"/*.py .
    cp -r "${./.}"/static .
    cp -r "${./.}"/templates .
    export PATH="${lib.makeBinPath shell.buildInputs}:$PATH"
    ${shell.shellHook}
  '';

  config = {
    WorkingDir = "/app";
    Env = [
      "PYTHONPATH=${python-venv}/lib"
      "PYTHONUNBUFFERED=1"
      "PYTHONDONTWRITEBYTECODE=1"
      "OSM_REVERT_VERSION_SUFFIX=docker-ui"
    ];
    Entrypoint = [ "python" "-m" "gunicorn" "main:app" ];
    Cmd = [
      "--bind"
      "0.0.0.0:8000"
      "--worker-class"
      "uvicorn.workers.UvicornWorker"
      "--graceful-timeout"
      "5"
      "--keep-alive"
      "300"
      "--forwarded-allow-ips"
      "*"
      "--access-logfile"
      "-"
    ];
  };
}
