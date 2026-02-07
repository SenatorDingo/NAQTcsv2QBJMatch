import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import os
import sys

SCRIPT_NAME = "naqt_csv_to_qbj.py"  # Ensure this is in the same folder as this GUI script

def select_csv_files():
    files = filedialog.askopenfilenames(
        title="Select NAQT CSV Files",
        filetypes=[("CSV Files", "*.csv")])
    if files:
        csv_listbox.delete(0, tk.END)
        for f in files:
            csv_listbox.insert(tk.END, f)

def select_output_folder():
    folder = filedialog.askdirectory(title="Select Output Folder")
    if folder:
        output_folder_var.set(folder)

def run_conversion():
    csv_files = csv_listbox.get(0, tk.END)
    output_folder = output_folder_var.get()
    if not csv_files:
        messagebox.showerror("Error", "Please select at least one CSV file.")
        return
    if not output_folder:
        messagebox.showerror("Error", "Please select an output folder.")
        return

    for csv_file in csv_files:
        out_path = os.path.join(output_folder, os.path.splitext(os.path.basename(csv_file))[0] + ".qbj")
        try:
            subprocess.check_call([
                sys.executable, SCRIPT_NAME, csv_file, out_path
            ])
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Error", f"Conversion failed for {csv_file}.\n{e}")
            return

    messagebox.showinfo("Success", "All files converted successfully!")

root = tk.Tk()
root.title("NAQT CSV to QBJ Converter GUI")

frame = tk.Frame(root, padx=10, pady=10)
frame.pack(fill=tk.BOTH, expand=True)

tk.Label(frame, text="1. Select CSV files:").pack(anchor="w")
csv_listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE, width=60, height=6)
csv_listbox.pack(fill=tk.X)
tk.Button(frame, text="Browse CSV Files", command=select_csv_files).pack(anchor="e", pady=(0, 10))

tk.Label(frame, text="2. Select output folder:").pack(anchor="w")
output_folder_var = tk.StringVar()
tk.Entry(frame, textvariable=output_folder_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True)
tk.Button(frame, text="Browse", command=select_output_folder).pack(side=tk.LEFT, padx=5)

tk.Label(frame, text="").pack()  # Spacer
tk.Button(frame, text="Convert", command=run_conversion, bg="#4CAF50", fg="white", height=2).pack(fill=tk.X, pady=10)

root.mainloop()