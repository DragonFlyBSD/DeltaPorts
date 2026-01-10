# Delta Ports

This repository contains patches and files that overlay and modify the FreeBSD Ports Collection to create DragonFly Ports.

> **Note:** This repository is not intended to be useful by itself. Scripts combine these overlays and patches to generate the final product.

## Organization

- **docs/**  
  DeltaPorts related documentation.

- **scripts/**  
  Shell scripts to generate the final DPorts repository, as well as a copy of the Tinderbox hooks.

  Also includes `scripts/dsynth-hooks/` which provides dsynth hook scripts to capture a bounded, high-signal “evidence bundle” (distilled errors + port context) when a port fails to build.

- **ports/**  
  Contains subdirectories corresponding to Ports categories (e.g., `audio`, `editors`, `devel`, etc.).

  - **<category>/**  
    Subdirectory of `ports/` (e.g., `audio`, `editors`, `devel`).

    - **<portname>/**  
      Subdirectory of a category directory. Contains a mix of:
      - `STATUS` file
      - `Makefile.DragonFly` file
      - `dragonfly/` directory
      - `diffs/` directory
      - `newport/` directory

      - **STATUS**  
        - 3 lines:
          1. `MASK`, `PORT`, or `DPORT`
              - `MASK`: Port will not have a counterpart in DPorts (subsequent lines may be comments).
              - `PORT`: Port is derived from FreeBSD ports.
              - `DPORT`: Port was created from scratch.
          2. `Last attempt: <version and revision of last build attempt>`
          3. `Last success: <version and revision of last successful build>` (blank if never built successfully)

      - **dragonfly/**  
        Functions like the port's `files/` directory. Contains patches applied after those in `files/`, and may also contain files.

      - **newport/**  
        Contains a Makefile, distinfo, pkg-descr, and other files for a port created from scratch. No `files/` subdirectory; `dragonfly/` is used instead.

      - **diffs/**  
        Contains `.diff` files (e.g., `distinfo.diff`, `pkg-plist.diff`) to modify corresponding port files. All filenames must end with `.diff`.

      - **Makefile.DragonFly**  
        Included after the Port Makefile. Used preferentially to `Makefile.diff`.

      - **REMOVE**  
        (Optional, inside `diffs/`) Lists files to remove after copying the port from FreeBSD. This avoids creating a `.diff` solely to remove a file, saving time and improving robustness against upstream changes.

- **special/**  
  Contains subdirectories (e.g., `Mk`, `Templates`) treated the same as a `<portname>` directory.
