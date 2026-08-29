@echo off
setlocal EnableExtensions

title Image PDF Tools
cd /d "%~dp0"

echo Image PDF Tools
echo.

if not exist "scripts\make-image-grid.py" (
    echo ERROR: scripts\make-image-grid.py was not found.
    echo Put this file in the same folder as the project scripts.
    echo.
    pause
    exit /b 1
)

if not exist "scripts\append-image-page.py" (
    echo ERROR: scripts\append-image-page.py was not found.
    echo Put this file in the same folder as the project scripts.
    echo.
    pause
    exit /b 1
)

if not exist "scripts\_common.py" (
    echo ERROR: scripts\_common.py was not found.
    echo Put this file in the same folder as the project scripts.
    echo.
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo ERROR: requirements.txt was not found.
    echo Put this file in the same folder as the project scripts.
    echo.
    pause
    exit /b 1
)

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    call :find_python
    if errorlevel 1 goto :end

    echo Setting up the local Python environment. This may take a minute.
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create the Python environment.
        echo Install Python 3.12 or 3.13 from https://www.python.org/downloads/windows/
        echo During install, select "Add python.exe to PATH".
        echo.
        pause
        exit /b 1
    )
)

call :ensure_dependencies
if errorlevel 1 goto :end

:menu
cls
echo Image PDF Tools
echo.
echo 1. Make an image grid from a folder
echo 2. Append a JPG image page to a PDF
echo 3. Make an image grid, then append it to a PDF
echo 4. Exit
echo.
set "CHOICE="
set /p "CHOICE=Choose 1, 2, 3, or 4: "

if "%CHOICE%"=="1" goto :make_grid
if "%CHOICE%"=="2" goto :append_image
if "%CHOICE%"=="3" goto :full_workflow
if "%CHOICE%"=="4" goto :end

echo.
echo Please choose 1, 2, 3, or 4.
pause
goto :menu

:make_grid
call :prompt_folder
if errorlevel 1 goto :menu

echo.
echo Creating image-grid.jpg in:
echo %IMAGE_FOLDER%
echo.
"%VENV_PY%" "scripts\make-image-grid.py" "%IMAGE_FOLDER%"
call :after_action
goto :menu

:append_image
call :prompt_image
if errorlevel 1 goto :menu
call :prompt_pdf
if errorlevel 1 goto :menu

echo.
echo Appending image to PDF.
echo.
"%VENV_PY%" "scripts\append-image-page.py" "%IMAGE_FILE%" --pdf "%PDF_FILE%"
call :after_action
goto :menu

:full_workflow
call :prompt_folder
if errorlevel 1 goto :menu
call :prompt_pdf
if errorlevel 1 goto :menu

echo.
echo Step 1 of 2: Creating image-grid.jpg in:
echo %IMAGE_FOLDER%
echo.
"%VENV_PY%" "scripts\make-image-grid.py" "%IMAGE_FOLDER%"
if errorlevel 1 (
    call :after_action
    goto :menu
)

set "GRID_FILE=%IMAGE_FOLDER%\image-grid.jpg"

echo.
echo Step 2 of 2: Appending image-grid.jpg to the PDF.
echo.
"%VENV_PY%" "scripts\append-image-page.py" "%GRID_FILE%" --pdf "%PDF_FILE%"
call :after_action
goto :menu

:prompt_folder
echo.
echo Enter the image folder path.
echo Tip: You can drag the folder into this window, then press Enter.
set "IMAGE_FOLDER="
set /p "IMAGE_FOLDER=Folder: "
rem Terminal drag-and-drop commonly wraps paths in quotes; remove them.
set "IMAGE_FOLDER=%IMAGE_FOLDER:"=%"
if "%IMAGE_FOLDER%"=="" exit /b 1
if not exist "%IMAGE_FOLDER%\" (
    echo.
    echo ERROR: Folder not found:
    echo %IMAGE_FOLDER%
    echo.
    pause
    exit /b 1
)
exit /b 0

:prompt_image
echo.
echo Enter the JPG/JPEG image path.
echo Tip: You can drag the image into this window, then press Enter.
set "IMAGE_FILE="
set /p "IMAGE_FILE=Image: "
rem Terminal drag-and-drop commonly wraps paths in quotes; remove them.
set "IMAGE_FILE=%IMAGE_FILE:"=%"
if "%IMAGE_FILE%"=="" exit /b 1
if not exist "%IMAGE_FILE%" (
    echo.
    echo ERROR: Image file not found:
    echo %IMAGE_FILE%
    echo.
    pause
    exit /b 1
)
exit /b 0

:prompt_pdf
echo.
echo Enter the PDF path.
echo Tip: You can drag the PDF into this window, then press Enter.
set "PDF_FILE="
set /p "PDF_FILE=PDF: "
rem Terminal drag-and-drop commonly wraps paths in quotes; remove them.
set "PDF_FILE=%PDF_FILE:"=%"
if "%PDF_FILE%"=="" exit /b 1
if not exist "%PDF_FILE%" (
    echo.
    echo ERROR: PDF file not found:
    echo %PDF_FILE%
    echo.
    pause
    exit /b 1
)
exit /b 0

:find_python
set "PYTHON_CMD="
py -3 -c "import sys; raise SystemExit(0 if (3, 12) <= sys.version_info < (3, 14) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    exit /b 0
)

python -c "import sys; raise SystemExit(0 if (3, 12) <= sys.version_info < (3, 14) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    exit /b 0
)

echo ERROR: Python 3.12 or 3.13 was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo During install, select "Add python.exe to PATH".
echo.
pause
exit /b 1

:ensure_dependencies
"%VENV_PY%" -c "import PIL, pillow_heif, pypdf" >nul 2>nul
if not errorlevel 1 exit /b 0

echo Installing required Python packages. This may take a minute.
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Could not install required Python packages.
    echo Check your internet connection, then run this file again.
    echo.
    pause
    exit /b 1
)
exit /b 0

:after_action
set "LAST_STATUS=%ERRORLEVEL%"
echo.
if not "%LAST_STATUS%"=="0" (
    echo The command did not complete successfully.
) else (
    echo Done.
)
echo.
pause
exit /b 0

:end
endlocal
