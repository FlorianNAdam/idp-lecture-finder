{
  pkgs ? import <nixpkgs> { },
}:
let
  lib = pkgs.lib;
  python = pkgs.python3;
  pyproject = lib.importTOML ./pyproject.toml;
in
let
  idp-lecture-finder = python.pkgs.buildPythonPackage {
    pname = pyproject.project.name;
    version = pyproject.project.version;
    src = ./.;

    pyproject = true;

    nativeBuildInputs = with python.pkgs; [
      setuptools
      wheel
    ];

    propagatedBuildInputs = with python.pkgs; [
      langchain
      langgraph
      langchain
      langchain-openai
      rich
      requests
      requests-cache
    ];

    doCheck = false;
  };
in
idp-lecture-finder
