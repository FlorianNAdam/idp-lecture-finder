{
  inputs = {
    nixpkgs.url = "github:cachix/devenv-nixpkgs/rolling";
    devenv.url = "github:cachix/devenv";
  };

  outputs =
    inputs@{ flake-parts, nixpkgs, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      imports = [
        inputs.devenv.flakeModule
      ];
      systems = nixpkgs.lib.systems.flakeExposed;

      perSystem =
        {
          config,
          self',
          inputs',
          pkgs,
          system,
          ...
        }:
        let
          idp-lecture-finder = import ./default.nix { inherit pkgs; };
        in
        {
          packages = {
            inherit idp-lecture-finder;
            default = idp-lecture-finder;
          };

          devenv.shells.default = {
            packages = with pkgs; [
              lldb
              llvmPackages.stdenv.cc
              gdb
            ];

            enterShell = ''
              export PYTHONPATH=$(lldb -P)
            '';

            languages.python = {
              enable = true;
              venv.enable = true;
              uv.enable = true;
            };
          };
        };
    };
}
