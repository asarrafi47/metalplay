"""MetalPlay graphical interface — free, built on tkinter (no extra dependencies)."""

from __future__ import annotations

import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from metalplay import __version__, paths
from metalplay.bottle import manager as bottles
from metalplay.config import Config
from metalplay.launcher import run as launcher
from metalplay.runtime import dxmt
from metalplay.runtime.installer import install_brew_wine_stable, install_free_runtime, setup_all
from metalplay.runtime.wine import check_rosetta, detect_installed_runtimes, get_runtime, system_info


class MetalPlayApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"MetalPlay — Windows Games via Metal")
        self.geometry("900x620")
        self.minsize(760, 520)
        self.config = Config.load()

        self._apply_style()
        self._build_ui()
        self.after(200, self.refresh_status)

    def _apply_style(self) -> None:
        self.configure(bg="#1e1e2e")
        style = ttk.Style(self)
        style.theme_use("clam")
        bg, fg, accent = "#1e1e2e", "#cdd6f4", "#89b4fa"
        panel = "#313244"
        style.configure(".", background=bg, foreground=fg, fieldbackground=panel)
        style.configure("TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("TLabel", background=bg, foreground=fg, font=("Helvetica", 12))
        style.configure("Title.TLabel", font=("Helvetica", 18, "bold"), foreground=accent)
        style.configure("Status.TLabel", font=("Menlo", 11))
        style.configure("TButton", font=("Helvetica", 12), padding=8)
        style.configure("Accent.TButton", font=("Helvetica", 12, "bold"), padding=10)
        style.map("TButton", background=[("active", panel)])
        style.configure("TCombobox", fieldbackground=panel, background=panel)
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", padding=[14, 8], font=("Helvetica", 11))

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(20, 16, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text="MetalPlay", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{__version__}  ·  DirectX → Metal", style="TLabel").pack(
            side="left", padx=(12, 0)
        )

        notebook = ttk.Notebook(self, padding=4)
        notebook.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self.home_tab = ttk.Frame(notebook, padding=16)
        self.bottles_tab = ttk.Frame(notebook, padding=16)
        self.launch_tab = ttk.Frame(notebook, padding=16)
        notebook.add(self.home_tab, text="  Home  ")
        notebook.add(self.bottles_tab, text="  Bottles  ")
        notebook.add(self.launch_tab, text="  Launch Game  ")

        self._build_home_tab()
        self._build_bottles_tab()
        self._build_launch_tab()

        log_frame = ttk.Frame(self, padding=(16, 0, 16, 12))
        log_frame.pack(fill="both", expand=False)
        ttk.Label(log_frame, text="Activity", style="TLabel").pack(anchor="w")
        self.log_text = tk.Text(
            log_frame, height=6, bg="#11111b", fg="#a6adc8",
            font=("Menlo", 10), relief="flat", wrap="word",
        )
        self.log_text.pack(fill="x", pady=(4, 0))
        self.log_text.configure(state="disabled")

    def _build_home_tab(self) -> None:
        ttk.Label(self.home_tab, text="System Status", style="Title.TLabel").pack(anchor="w")
        self.status_label = ttk.Label(self.home_tab, text="Checking...", style="Status.TLabel", wraplength=800)
        self.status_label.pack(anchor="w", pady=(12, 20))

        btn_row = ttk.Frame(self.home_tab)
        btn_row.pack(anchor="w", pady=4)

        ttk.Button(btn_row, text="Refresh", command=self.refresh_status).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Quick Setup (Free)", style="Accent.TButton", command=self.run_quick_setup).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(btn_row, text="Install Gcenx Wine", command=lambda: self._run_async(self._install_gcenx)).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(btn_row, text="Install Wine Stable (brew)", command=lambda: self._run_async(self._install_brew)).pack(
            side="left"
        )

        ttk.Label(self.home_tab, text="\nFree runtime options:", style="TLabel").pack(anchor="w")
        ttk.Label(
            self.home_tab,
            text=(
                "• Gcenx Wine — best DXMT/Metal support (recommended, ~180 MB download)\n"
                "• Wine Stable — Homebrew install, fallback option\n"
                "Both are free. Gcenx is more efficient for DirectX 11 games on Apple Silicon."
            ),
            style="Status.TLabel",
            wraplength=820,
        ).pack(anchor="w", pady=(4, 0))

    def _build_bottles_tab(self) -> None:
        top = ttk.Frame(self.bottles_tab)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="Wine Bottles", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_bottles).pack(side="right", padx=(8, 0))
        ttk.Button(top, text="Create Bottle", command=self.create_bottle_dialog).pack(side="right")

        self.bottle_list = tk.Listbox(
            self.bottles_tab, bg="#313244", fg="#cdd6f4",
            font=("Menlo", 12), relief="flat", height=12,
            selectbackground="#89b4fa", selectforeground="#1e1e2e",
        )
        self.bottle_list.pack(fill="both", expand=True, pady=(0, 12))

        actions = ttk.Frame(self.bottles_tab)
        actions.pack(fill="x")
        ttk.Button(actions, text="Delete Selected", command=self.delete_bottle).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Open winecfg", command=self.open_winecfg).pack(side="left")

    def _build_launch_tab(self) -> None:
        ttk.Label(self.launch_tab, text="Launch a Game", style="Title.TLabel").pack(anchor="w", pady=(0, 16))

        form = ttk.Frame(self.launch_tab)
        form.pack(fill="x")

        ttk.Label(form, text="Executable (.exe):").grid(row=0, column=0, sticky="w", pady=6)
        self.exe_var = tk.StringVar()
        exe_entry = ttk.Entry(form, textvariable=self.exe_var, width=58)
        exe_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(form, text="Browse…", command=self.browse_exe).grid(row=0, column=2)

        ttk.Label(form, text="Bottle:").grid(row=1, column=0, sticky="w", pady=6)
        self.bottle_var = tk.StringVar(value=self.config.default_bottle or "")
        self.bottle_combo = ttk.Combobox(form, textvariable=self.bottle_var, width=40)
        self.bottle_combo.grid(row=1, column=1, sticky="w", padx=(8, 0))

        ttk.Label(form, text="Graphics:").grid(row=2, column=0, sticky="w", pady=6)
        self.graphics_var = tk.StringVar(value=self.config.default_graphics)
        gfx = ttk.Combobox(
            form, textvariable=self.graphics_var,
            values=["dxmt", "moltenvk", "wined3d", "auto"], width=20, state="readonly",
        )
        gfx.grid(row=2, column=1, sticky="w", padx=(8, 0))

        form.columnconfigure(1, weight=1)

        ttk.Label(
            self.launch_tab,
            text="dxmt = Direct3D 11 → Metal (fastest)   ·   moltenvk = D3D12 → Metal",
            style="Status.TLabel",
        ).pack(anchor="w", pady=(12, 0))

        ttk.Button(
            self.launch_tab, text="▶  Launch Game", style="Accent.TButton", command=self.launch_game
        ).pack(anchor="w", pady=(20, 0))

    # ── Actions ──────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        def _append() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _append)

    def _run_async(self, fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def refresh_status(self) -> None:
        info = system_info()
        runtimes = detect_installed_runtimes()
        dxmt_ok = dxmt.is_installed()
        bottle_count = len(bottles.list_bottles())
        rosetta = check_rosetta() if info["arch"] == "arm64" else True

        lines = [
            f"macOS {info['macos']}  ·  {info['arch']}  ·  Rosetta: {'✓' if rosetta else '✗'}",
            f"DXMT (DirectX→Metal): {'✓ installed' if dxmt_ok else '✗ not installed'}",
            f"Wine runtimes: {len(runtimes)} detected",
        ]
        for rt in runtimes:
            cap = "Metal ✓" if rt.is_metal_capable() else "Metal ?"
            lines.append(f"  • {rt.name} — {rt.version()} [{cap}]")
        if not runtimes:
            lines.append("  • None — click Quick Setup to install free Wine")
        lines.append(f"Bottles: {bottle_count}")

        self.status_label.configure(text="\n".join(lines))
        self.refresh_bottles()

    def refresh_bottles(self) -> None:
        self.bottle_list.delete(0, "end")
        names: list[str] = []
        for name, path, meta in bottles.list_bottles():
            gfx = meta.graphics if meta else "?"
            self.bottle_list.insert("end", f"{name}  [{gfx}]")
            names.append(name)
        self.bottle_combo["values"] = names
        if names and not self.bottle_var.get():
            self.bottle_var.set(names[0])

    def run_quick_setup(self) -> None:
        self._run_async(self._quick_setup)

    def _quick_setup(self) -> None:
        try:
            self.log("Starting free quick setup...")
            result = setup_all(callback=self.log)
            self.log(f"Done! Wine {result['version']} ready with DXMT.")
            self.after(0, self.refresh_status)
            self.after(0, lambda: messagebox.showinfo("Setup Complete", "MetalPlay is ready.\nCreate a bottle, then launch a game."))
        except Exception as exc:
            self.log(f"Setup failed: {exc}")
            self.after(0, lambda: messagebox.showerror("Setup Failed", str(exc)))

    def _install_gcenx(self) -> None:
        try:
            self.log("Installing Gcenx Wine...")
            runtime = install_free_runtime(prefer="gcenx", callback=self.log)
            dxmt.install_into_wine(runtime)
            self.log("Gcenx Wine + DXMT ready.")
            self.after(0, self.refresh_status)
        except Exception as exc:
            self.log(f"Error: {exc}")

    def _install_brew(self) -> None:
        try:
            self.log("Installing Wine Stable via Homebrew...")
            runtime = install_brew_wine_stable(callback=self.log)
            if runtime:
                if dxmt.is_installed():
                    dxmt.install_into_wine(runtime)
                self.log("Wine Stable ready.")
            self.after(0, self.refresh_status)
        except Exception as exc:
            self.log(f"Error: {exc}")

    def create_bottle_dialog(self) -> None:
        if not detect_installed_runtimes():
            messagebox.showwarning("No Wine", "Install a Wine runtime first (Quick Setup on Home tab).")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Create Bottle")
        dialog.geometry("360x180")
        dialog.configure(bg="#1e1e2e")
        dialog.transient(self)
        dialog.grab_set()

        name_var = tk.StringVar(value="gaming")
        ttk.Label(dialog, text="Bottle name:").pack(anchor="w", padx=20, pady=(20, 4))
        ttk.Entry(dialog, textvariable=name_var, width=30).pack(padx=20, anchor="w")

        def do_create() -> None:
            name = name_var.get().strip()
            if not name:
                return
            dialog.destroy()
            self._run_async(lambda: self._create_bottle(name))

        ttk.Button(dialog, text="Create", command=do_create).pack(pady=20)

    def _create_bottle(self, name: str) -> None:
        try:
            runtime = get_runtime(self.config.wine_runtime)
            if not runtime:
                runtime = detect_installed_runtimes()[0]
            self.log(f"Creating bottle '{name}'...")
            bottles.create(name, runtime, graphics="dxmt")
            self.config.default_bottle = name
            self.config.save()
            self.log(f"Bottle '{name}' created.")
            self.after(0, self.refresh_bottles)
        except Exception as exc:
            self.log(f"Failed: {exc}")

    def delete_bottle(self) -> None:
        sel = self.bottle_list.curselection()
        if not sel:
            return
        label = self.bottle_list.get(sel[0])
        name = label.split()[0]
        if messagebox.askyesno("Delete Bottle", f"Delete bottle '{name}' and all its data?"):
            try:
                bottles.remove(name)
                self.refresh_bottles()
            except Exception as exc:
                messagebox.showerror("Error", str(exc))

    def open_winecfg(self) -> None:
        sel = self.bottle_list.curselection()
        if not sel:
            messagebox.showinfo("Select a bottle", "Select a bottle first.")
            return
        name = self.bottle_list.get(sel[0]).split()[0]
        runtime = get_runtime(self.config.wine_runtime) or detect_installed_runtimes()[0]
        bottles.run_wine(runtime, bottles.bottle_path(name), ["winecfg"])

    def browse_exe(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Windows executable",
            filetypes=[("Windows executables", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.exe_var.set(path)

    def launch_game(self) -> None:
        exe = self.exe_var.get().strip()
        bottle_name = self.bottle_var.get().strip()
        if not exe:
            messagebox.showwarning("Missing executable", "Select a .exe file to launch.")
            return
        if not bottle_name:
            messagebox.showwarning("Missing bottle", "Select or create a bottle first.")
            return
        if not Path(exe).exists() and not exe.startswith("C:\\"):
            messagebox.showwarning("File not found", f"Could not find:\n{exe}")
            return

        runtime = get_runtime(self.config.wine_runtime)
        if not runtime:
            messagebox.showwarning("No Wine", "Install Wine first (Home → Quick Setup).")
            return

        bottle = bottles.bottle_path(bottle_name)
        if not bottle.is_dir():
            messagebox.showerror("Bottle not found", f"Bottle '{bottle_name}' does not exist.")
            return

        self.log(f"Launching {Path(exe).name} in bottle '{bottle_name}'...")
        self._run_async(lambda: self._launch(runtime, bottle, exe))

    def _launch(self, runtime, bottle, exe: str) -> None:
        try:
            code = launcher.launch(
                runtime, bottle, exe,
                config=self.config,
                graphics=self.graphics_var.get(),
            )
            self.log(f"Game exited with code {code}")
        except Exception as exc:
            self.log(f"Launch error: {exc}")


def main() -> None:
    paths.ensure_dirs()
    app = MetalPlayApp()
    app.mainloop()


if __name__ == "__main__":
    main()
