@echo off
rem Build DoctorHide.exe with PyInstaller (Python required).
cd /d "%~dp0"

python -m pip install --quiet --disable-pip-version-check pyinstaller || goto :err
python -m PyInstaller --noconfirm --clean --onefile --console --name DoctorHide ^
  --distpath dist --workpath build --specpath . ^
  --add-data "gui/index.html;gui" --add-data "gui/spy_pipboy_200x200.gif;gui" ^
  --hidden-import gui.server --collect-submodules doctorhide ^
  --exclude-module cv2 --exclude-module numpy --exclude-module indra_ocr ^
  launcher.py || goto :err

echo Build OK: dist\DoctorHide.exe
exit /b 0

:err
echo Build failed.
exit /b 1
