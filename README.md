# Windows Audio Path Checker

A small, local Windows tool for the specific problem where the **Test** button
in Sound settings plays through your headphones, but YouTube, a browser, or
another app is silent.

The Windows test proves the endpoint and basic driver path can play sound.
Normal apps use a separate shared audio session, with their own volume and
possibly their own output-device assignment. That difference is what this tool
checks.

## What it checks

- Windows Audio and Audio Endpoint Builder service status
- The default Windows playback endpoint and master mute/volume
- Output devices visible to normal apps through PortAudio/WASAPI
- Active per-app audio sessions, including Chrome, Edge, Firefox, Brave, Opera,
  and Vivaldi
- Browser sessions that are muted or set near zero
- A possible default-output mismatch between the system and app paths
- A Python app test tone through any detected WASAPI output
- A browser test tone through the same browser path used by YouTube

Everything runs locally. The checker does not upload a report or collect
browsing history.

## Quick start

Requirements: Windows 10/11 and Python 3.10 or newer.

1. Download or clone this repository.
2. Double-click `run_checker.bat`.
3. Start a YouTube video and leave it playing.
4. Select **Scan again** in the checker.
5. Select your headphones and use **Test app sound**.
6. Use **Test browser sound**, then select the button on the page that opens.

The first launch creates a private `.venv` folder inside the project and
installs the four Python dependencies.

You can also run it from a terminal:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\python -m audio_path_checker
```

To create a JSON-only report:

```powershell
.\.venv\Scripts\python -m audio_path_checker --no-gui --report audio-report.json
```

The command returns exit code `2` when it finds a critical item.

## Reading the two sound tests

| Windows Test | App test | Browser test | Most likely explanation |
|---|---|---|---|
| Audible | Audible | Silent | Browser session muted, routed elsewhere, or the tab/site is muted |
| Audible | Silent | Silent | Normal apps are using another output, or shared-mode audio is failing |
| Audible | Audible | Audible | YouTube player/tab mute or a site-specific problem |
| Silent | Silent | Silent | Endpoint, connection, master volume, driver, or Windows Audio service |

For the screenshot described in this issue—Windows Test works while all normal
sources are silent—the first thing to open is **Volume mixer**. Check both the
app volume and the app's selected Output device.

## Safe fixes included

- **Unmute browser sessions** changes only recognized browser sessions. It
  unmutes them and raises sessions below 50% to 50%.
- **Open Volume mixer** opens `ms-settings:apps-volume`, where Windows exposes
  per-app output routing.
- **Open Sound settings** opens the normal system sound page.

The checker intentionally does not reinstall drivers, edit the registry, change
undocumented persistent routing data, or restart services automatically.

## If the checker does not solve it

Try these in order:

1. In Volume mixer, set the browser Output device to **Default** or directly to
   the headphones. Check its app slider and mute button.
2. Right-click the browser tab and make sure the site/tab is not muted. Check
   YouTube's own speaker button and player slider.
3. Close every browser window completely, reopen it, and scan again.
4. In the headphone properties, turn **Audio enhancements** off temporarily.
5. Restart **Windows Audio** and **Windows Audio Endpoint Builder**, or restart
   the PC.
6. Run Microsoft's Audio troubleshooter in the Get Help app.
7. Update the headphone/USB/Bluetooth audio driver from the PC or headset
   manufacturer.

Microsoft references:

- [Fix application audio when system sounds work](https://support.microsoft.com/en-us/windows/hardware/audio/fix-app-audio-not-working-while-system-sounds-work-in-windows)
- [Fix audio issues with speakers or headphones](https://support.microsoft.com/en-us/windows/hardware/audio/fix-audio-issues-when-no-sound-plays-from-speakers-or-headphones-in-windows)
- [Windows Settings URI reference](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings)

## Related open-source projects

- [EarTrumpet](https://github.com/File-New-Project/EarTrumpet) is the best
  ready-made option for seeing app volumes and moving apps between playback
  devices. Try it if you want a polished daily volume mixer rather than a
  diagnostic report.
- [pycaw](https://github.com/AndreMiras/pycaw) exposes Windows Core Audio
  sessions to Python and powers this checker's app-session inspection.
- [python-sounddevice](https://github.com/spatialaudio/python-sounddevice)
  provides the app-level playback test.
- [SoundSwitch](https://github.com/Belphemur/SoundSwitch) is useful when Windows
  keeps selecting the wrong default playback device.
- [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) adds WASAPI loopback
  capture for deeper analysis of what Windows is sending to an output.

None of these can repair broken headphone hardware. In this particular symptom,
however, working Windows test audio makes hardware failure less likely than
per-app mute or output routing.

## Development

The diagnosis rules are platform-independent and unit-tested:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q audio_path_checker
```

GitHub Actions runs the same checks on Windows with Python 3.11 and 3.13.

## License

MIT
