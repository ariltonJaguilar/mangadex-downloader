"""Desktop interface for the existing downloader command line."""

import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from urllib.parse import urlparse
from uuid import UUID

from .language import Language


FORMAT_OPTIONS = {
    "CBZ — arquivo único": "cbz-single",
    "EPUB — arquivo único": "epub-single",
    "PDF — arquivo único": "pdf-single",
    "Imagens — uma pasta": "raw-single",
}
PDF_LAYOUT_OPTIONS = {
    "Páginas separadas (mangá)": "pages",
    "Tiras verticais (webcomic)": "webcomic",
}
PROGRESS_MARKER = "@@MANGADEX_GUI_PROGRESS@@"


def default_settings_path():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config"))
    return base / "mangadex-downloader" / "gui-settings.json"


def build_arguments(url, destination, language, fmt, start="", end="", compressed=False,
                    title="", author="", custom_cover="", fallback_english=False,
                    pdf_page_width="auto", pdf_layout="pages"):
    """Validate desktop input and produce shell-free CLI arguments."""
    url = url.strip()
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme not in ("http", "https")
            or parsed.netloc.lower() != "mangadex.org"
            or len(parts) < 2 or parts[0] not in ("title", "chapter", "list")):
        raise ValueError("Cole um link do MangaDex de mangá, capítulo ou lista.")
    try:
        UUID(parts[1])
    except ValueError:
        raise ValueError("O identificador no link do MangaDex é inválido.") from None
    if not destination.strip():
        raise ValueError("Escolha uma pasta para salvar os downloads.")
    numbers = []
    args = [url, "--path", str(Path(destination).expanduser().resolve()),
            "--language", language, "--save-as", fmt,
            "--filename-single", "{manga.title}{file_ext}"]
    for label, value, flag in (("inicial", start, "--start-chapter"),
                               ("final", end, "--end-chapter")):
        if value.strip():
            try:
                number = float(value.replace(",", "."))
            except ValueError:
                raise ValueError(f"O capítulo {label} deve ser um número.") from None
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"O capítulo {label} deve ser um número positivo ou zero.")
            args.extend([flag, str(number)])
            numbers.append(number)
    if len(numbers) == 2 and numbers[0] > numbers[1]:
        raise ValueError("O capítulo inicial não pode ser maior que o final.")
    if compressed:
        args.append("--use-compressed-image")
    if fallback_english and language != "en":
        args.append("--fallback-english")
    if fmt.startswith("pdf"):
        if str(pdf_page_width).strip().lower() in ("", "auto", "automática", "automatico"):
            page_width = 0
        else:
            try:
                page_width = int(pdf_page_width)
            except (TypeError, ValueError):
                raise ValueError("A largura das páginas PDF deve ser automática ou um número inteiro.") from None
        if page_width and not 600 <= page_width <= 4000:
            raise ValueError("A largura do PDF deve ficar entre 600 e 4000 pixels.")
        if pdf_layout not in ("pages", "webcomic"):
            raise ValueError("O modo de PDF selecionado é inválido.")
        args.extend(["--pdf-page-width", str(page_width), "--pdf-layout", pdf_layout])
    if title.strip():
        args.extend(["--override-title", title.strip()])
    if author.strip():
        args.extend(["--override-author", author.strip()])
    if custom_cover.strip():
        cover_path = Path(custom_cover).expanduser().resolve()
        if not cover_path.is_file():
            raise ValueError("A imagem de capa selecionada não foi encontrada.")
        args.extend(["--custom-cover", str(cover_path)])
    return args


class DownloaderWindow:
    def __init__(self, root, settings_path=None):
        self.root = root
        self.settings_path = Path(settings_path) if settings_path else default_settings_path()
        self.process = None
        self.events = queue.Queue(maxsize=2000)
        self.cancelled = False
        self.closing = False
        self.progress_output_buffer = ""
        root.title("MangaDex • Downloader")
        root.geometry("900x820")
        root.minsize(620, 480)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TButton", padding=8)
        style.configure("TLabel", font=("Segoe UI", 10))
        scroll_container = ttk.Frame(root)
        scroll_container.pack(fill="both", expand=True)
        scroll_container.rowconfigure(0, weight=1)
        scroll_container.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            scroll_container, highlightthickness=0,
            background=style.lookup("TFrame", "background"),
        )
        scrollbar = ttk.Scrollbar(
            scroll_container, orient="vertical", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        outer = ttk.Frame(self.canvas, padding=24)
        self.canvas_window = self.canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_scroll_content)
        root.bind("<MouseWheel>", self._scroll_with_mouse)
        ttk.Label(outer, text="MangaDex Downloader", font=("Segoe UI", 23, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Seus mangás, prontos para ler offline.").pack(anchor="w", pady=(0, 20))
        self.url = tk.StringVar()
        self.destination = tk.StringVar(value=str(Path.home() / "Downloads" / "MangaDex"))
        self.language = tk.StringVar(value="Português (Brasil)")
        self.fmt = tk.StringVar(value="CBZ — arquivo único")
        self.start = tk.StringVar()
        self.end = tk.StringVar()
        self.compressed = tk.BooleanVar()
        self.fallback_english = tk.BooleanVar()
        self.title = tk.StringVar()
        self.author = tk.StringVar()
        self.custom_cover = tk.StringVar()
        self.pdf_layout = tk.StringVar(value="Páginas separadas (mangá)")
        self.languages = {"Português (Brasil)": "pt-br", "Português (Portugal)": "pt",
                          "Inglês": "en", "Espanhol (América Latina)": "es-la"}
        self.languages.update({lang.name: lang.value or "Other" for lang in Language
                               if lang.value not in self.languages.values()})
        self.load_settings()
        ttk.Label(outer, text="Link do mangá, capítulo ou lista").pack(anchor="w")
        url_row = ttk.Frame(outer)
        url_row.pack(fill="x", pady=(5, 12))
        ttk.Entry(url_row, textvariable=self.url).pack(side="left", fill="x", expand=True)
        self.fetch_button = ttk.Button(url_row, text="Buscar informações", command=self.fetch_metadata)
        self.fetch_button.pack(side="left", padx=(8, 0))
        metadata = ttk.LabelFrame(outer, text="Informações do arquivo", padding=10)
        metadata.pack(fill="x", pady=(0, 12))
        metadata.columnconfigure(1, weight=1)
        ttk.Label(metadata, text="Título").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(metadata, textvariable=self.title).grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(metadata, text="Autor(es)").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(metadata, textvariable=self.author).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(metadata, text="Capa").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(metadata, textvariable=self.custom_cover).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(metadata, text="Escolher imagem", command=self.choose_cover).grid(row=2, column=2, padx=(8, 0), pady=4)
        ttk.Label(metadata, text="Deixe título/autor vazios para usar os dados do MangaDex; capa vazia usa a capa oficial.").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(outer, text="Salvar em").pack(anchor="w")
        folder = ttk.Frame(outer)
        folder.pack(fill="x", pady=(5, 12))
        ttk.Entry(folder, textvariable=self.destination).pack(side="left", fill="x", expand=True)
        ttk.Button(folder, text="Escolher pasta", command=self.choose_folder).pack(side="left", padx=(8, 0))
        options = ttk.Frame(outer)
        options.pack(fill="x")
        for column, (label, var, choices) in enumerate([
            ("Idioma", self.language, list(self.languages)),
            ("Formato de saída", self.fmt, list(FORMAT_OPTIONS)),
            ("Capítulo inicial", self.start, None), ("Capítulo final", self.end, None),
        ]):
            options.columnconfigure(column, weight=1)
            ttk.Label(options, text=label).grid(row=0, column=column, sticky="w", padx=(0, 12))
            widget = (ttk.Combobox(options, textvariable=var, values=choices, state="readonly", width=22)
                      if choices else ttk.Entry(options, textvariable=var, width=12))
            widget.grid(row=1, column=column, sticky="ew", padx=(0, 12), pady=5)
        ttk.Label(
            outer,
            text="Capítulos em branco: baixa todos. CBZ, EPUB e PDF: um arquivo único.",
        ).pack(anchor="w")
        pdf_options = ttk.LabelFrame(outer, text="PDF para iPad", padding=10)
        pdf_options.pack(fill="x", pady=(10, 4))
        pdf_options.columnconfigure(1, weight=1)
        ttk.Label(pdf_options, text="Leitura").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            pdf_options, textvariable=self.pdf_layout, values=list(PDF_LAYOUT_OPTIONS),
            state="readonly", width=28,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(
            pdf_options,
            text="Largura automática pela maior página; a capa não altera a medida.",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(
            pdf_options,
            text="Mangá mantém uma imagem por página; webcomic reúne cada capítulo em tiras longas.",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Checkbutton(outer, text="Usar imagens comprimidas (menor tamanho)", variable=self.compressed).pack(anchor="w", pady=10)
        ttk.Checkbutton(
            outer, text="Usar capítulos em inglês quando faltarem no idioma escolhido",
            variable=self.fallback_english,
        ).pack(anchor="w", pady=(0, 10))
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 12))
        self.download_button = ttk.Button(actions, text="Iniciar download", command=self.download)
        self.download_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Cancelar", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(actions, text="Abrir pasta", command=self.open_folder).pack(side="right")
        self.status = tk.StringVar(value="Pronto para baixar")
        ttk.Label(outer, textvariable=self.status).pack(anchor="w")
        self.progress_detail = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.progress_detail).pack(anchor="w")
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=8)
        self.log = scrolledtext.ScrolledText(outer, height=12, state="disabled", wrap="word",
                                           background="#17202b", foreground="#e6edf3", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)
        reply = ttk.Frame(outer)
        reply.pack(fill="x", pady=(8, 0))
        ttk.Label(reply, text="Se o programa pedir uma escolha:").pack(side="left")
        self.reply = ttk.Entry(reply)
        self.reply.pack(side="left", fill="x", expand=True, padx=8)
        self.reply.bind("<Return>", lambda event: self.send_reply())
        self.reply_button = ttk.Button(reply, text="Enviar", command=self.send_reply, state="disabled")
        self.reply_button.pack(side="left")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(80, self.poll)

    def _update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_scroll_content(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _scroll_with_mouse(self, event):
        if isinstance(event.widget, tk.Text):
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def load_settings(self):
        try:
            values = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        fields = {
            "url": self.url, "destination": self.destination,
            "language": self.language, "format": self.fmt,
            "start": self.start, "end": self.end,
            "title": self.title, "author": self.author, "custom_cover": self.custom_cover,
            "pdf_layout": self.pdf_layout,
        }
        old_formats = {"cbz": "CBZ — arquivo único", "epub": "EPUB — arquivo único",
                       "pdf": "PDF — arquivo único", "raw": "Imagens — uma pasta"}
        if values.get("format") in old_formats:
            values["format"] = old_formats[values["format"]]
        for name, variable in fields.items():
            value = values.get(name)
            if isinstance(value, str):
                variable.set(value)
        if self.language.get() not in self.languages:
            self.language.set("Português (Brasil)")
        if self.fmt.get() not in FORMAT_OPTIONS:
            self.fmt.set("CBZ — arquivo único")
        if self.pdf_layout.get() not in PDF_LAYOUT_OPTIONS:
            self.pdf_layout.set("Páginas separadas (mangá)")
        if isinstance(values.get("compressed"), bool):
            self.compressed.set(values["compressed"])
        if isinstance(values.get("fallback_english"), bool):
            self.fallback_english.set(values["fallback_english"])

    def save_settings(self):
        values = {
            "url": self.url.get(), "destination": self.destination.get(),
            "language": self.language.get(), "format": self.fmt.get(),
            "start": self.start.get(), "end": self.end.get(),
            "compressed": self.compressed.get(),
            "fallback_english": self.fallback_english.get(),
            "title": self.title.get(), "author": self.author.get(),
            "custom_cover": self.custom_cover.get(),
            "pdf_layout": self.pdf_layout.get(),
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.settings_path)
        except OSError:
            self.status.set("Não foi possível salvar as configurações.")

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            self.destination.set(folder)

    def choose_cover(self):
        filename = filedialog.askopenfilename(
            parent=self.root, title="Escolher capa",
            filetypes=(("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Todos", "*.*")),
        )
        if filename:
            self.custom_cover.set(filename)

    def fetch_metadata(self):
        try:
            url = self.url.get().strip()
            parts = urlparse(url).path.strip("/").split("/")
            if len(parts) < 2 or parts[0] != "title":
                raise ValueError("Use o link de um mangá para buscar as informações.")
            manga_id = str(UUID(parts[1]))
        except (ValueError, IndexError) as error:
            messagebox.showerror("Link inválido", str(error), parent=self.root)
            return
        self.fetch_button.configure(state="disabled")
        self.status.set("Buscando título e autor no MangaDex…")
        threading.Thread(target=self._fetch_metadata_worker, args=(manga_id,), daemon=True).start()

    def _fetch_metadata_worker(self, manga_id):
        try:
            import requests

            response = requests.get(
                f"https://api.mangadex.org/manga/{manga_id}",
                params={"includes[]": ["author"]}, timeout=20,
            )
            response.raise_for_status()
            data = response.json()["data"]
            titles = data["attributes"]["title"]
            title = titles.get("pt-br") or titles.get("en") or next(iter(titles.values()))
            authors = [rel.get("attributes", {}).get("name", "")
                       for rel in data.get("relationships", []) if rel.get("type") == "author"]
            self.events.put(("metadata", (title, ", ".join(filter(None, authors)))))
        except Exception as error:
            self.events.put(("metadata_error", str(error)))

    def open_folder(self):
        folder = Path(self.destination.get()).expanduser()
        if not folder.is_dir():
            messagebox.showinfo("Pasta", "A pasta ainda não existe.", parent=self.root)
            return
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])

    def download(self):
        if self.process is not None:
            return
        try:
            output_format = FORMAT_OPTIONS[self.fmt.get()]
            if output_format == "epub-single":
                from .format.epub import epub_ready

                if not epub_ready:
                    raise ValueError(
                        "O formato EPUB precisa das dependências opcionais. "
                        "Instale-as com: .venv\\Scripts\\python.exe -m pip install "
                        "-r requirements-optional.txt"
                    )
            args = build_arguments(self.url.get(), self.destination.get(),
                                   self.languages[self.language.get()], output_format,
                                   self.start.get(), self.end.get(), self.compressed.get(),
                                   self.title.get(), self.author.get(), self.custom_cover.get(),
                                   self.fallback_english.get(), "auto",
                                   PDF_LAYOUT_OPTIONS[self.pdf_layout.get()])
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            env = dict(
                os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                MANGADEXDL_GUI="1",
            )
            self.process = subprocess.Popen(
                [str(executable), "-u", "-m", "mangadex_downloader", *args],
                cwd=str(Path(__file__).resolve().parent.parent), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (ValueError, OSError) as error:
            messagebox.showerror("Não foi possível iniciar", str(error), parent=self.root)
            return
        self.save_settings()
        self.cancelled = False
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status.set("Baixando — acompanhe os detalhes abaixo")
        self.progress_detail.set("Preparando e contando os capítulos…")
        self.progress.configure(mode="indeterminate", value=0)
        self.download_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.reply_button.configure(state="normal")
        self.progress.start(12)
        threading.Thread(target=self.read_output, args=(self.process,), daemon=True).start()

    def read_output(self, process):
        try:
            while True:
                char = process.stdout.read(1)
                if not char:
                    break
                self.events.put(("output", char))
        finally:
            process.stdout.close()
            self.events.put(("done", process.wait()))

    def poll(self):
        output = []
        for _ in range(4000):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "output":
                output.append(value)
            elif kind == "metadata":
                self.title.set(value[0])
                self.author.set(value[1])
                self.status.set("Informações carregadas; você pode editá-las.")
                self.fetch_button.configure(state="normal")
            elif kind == "metadata_error":
                self.status.set(f"Não foi possível buscar as informações: {value}")
                self.fetch_button.configure(state="normal")
            else:
                if self.progress_output_buffer:
                    output.append(self.progress_output_buffer)
                    self.progress_output_buffer = ""
                self.process.stdin.close()
                self.process = None
                self.progress.stop()
                self.download_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                self.reply_button.configure(state="disabled")
                self.status.set("Download cancelado" if self.cancelled else
                                "Download concluído" if value == 0 else
                                "Falha no download — consulte os detalhes abaixo")
                if value == 0 and not self.cancelled:
                    self.progress.configure(mode="determinate", value=self.progress["maximum"])
                    self.progress_detail.set("100% — download concluído")
                elif self.cancelled:
                    self.progress_detail.set("Download interrompido")
                if self.closing:
                    self.root.destroy()
                    return
        if output:
            visible_output = self._consume_progress_output("".join(output))
            self.log.configure(state="normal")
            self.log.insert("end", re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", visible_output))
            if int(self.log.index("end-1c").split(".")[0]) > 2000:
                self.log.delete("1.0", "500.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(80, self.poll)

    def _consume_progress_output(self, text):
        data = self.progress_output_buffer + text
        self.progress_output_buffer = ""
        visible = []
        while data:
            marker_at = data.find(PROGRESS_MARKER)
            if marker_at < 0:
                held = 0
                for size in range(1, min(len(data), len(PROGRESS_MARKER) - 1) + 1):
                    if PROGRESS_MARKER.startswith(data[-size:]):
                        held = size
                visible.append(data[:-held] if held else data)
                self.progress_output_buffer = data[-held:] if held else ""
                break
            visible.append(data[:marker_at])
            line_end = data.find("\n", marker_at)
            if line_end < 0:
                self.progress_output_buffer = data[marker_at:]
                break
            payload = data[marker_at + len(PROGRESS_MARKER):line_end].strip()
            try:
                self._update_chapter_progress(json.loads(payload))
            except (ValueError, TypeError, KeyError):
                visible.append(data[marker_at:line_end + 1])
            data = data[line_end + 1:]
        return "".join(visible)

    def _update_chapter_progress(self, payload):
        current = int(payload["current"])
        total = int(payload["total"])
        if total <= 0:
            return
        percent = round(current * 100 / total)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=total, value=current)
        chapter = str(payload.get("chapter", "")).strip()
        if current >= total:
            detail = f"100% — {total}/{total} capítulos baixados; criando o arquivo final…"
        else:
            detail = f"{percent}% — {current}/{total} capítulos concluídos"
            if chapter:
                detail += f" — baixando {chapter}"
        self.progress_detail.set(detail)

    def send_reply(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self.process.stdin.write(self.reply.get() + "\n")
            self.process.stdin.flush()
            self.reply.delete(0, "end")
        except (OSError, ValueError):
            self.status.set("Não foi possível enviar a resposta ao programa.")

    def cancel(self):
        if self.process is not None and self.process.poll() is None:
            self.cancelled = True
            self.status.set("Cancelando…")
            self.process.terminate()
            process = self.process
            self.root.after(3000, lambda: process.kill() if process.poll() is None else None)

    def close(self):
        self.save_settings()
        self.closing = True
        if self.process is not None:
            self.cancel()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    DownloaderWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
