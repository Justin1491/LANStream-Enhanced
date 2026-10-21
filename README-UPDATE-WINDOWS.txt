LANStream Enhanced - update an existing Windows x64 installation
================================================================

This package contains the updated application and its complete Python runtime.
It uses your existing FFmpeg/FFprobe and 7-Zip tools. It is not a first-install
bundle and does not contain config.txt, cache, passwords or media.

1. Close the diagnostic LANStream window on port 8062 and the installed Enhanced
   window on port 8061. Leave the original C:\LANStream application on 8060 alone.
   If an instance is hard to locate, the included Check-LANStreamProcesses.ps1
   can identify its path; see README-PROCESS-CHECK.md.
2. Back up the complete C:\LANStream-Enhanced folder to a dated sibling folder,
   for example C:\LANStream-Enhanced.backup-20261021. Keep this backup private:
   it contains your password, settings and Favorites. Keep it for rollback.
3. In the downloaded ZIP's Properties, select Unblock if shown, then extract
   the entire ZIP into a fresh folder outside C:\LANStream-Enhanced.
4. In C:\LANStream-Enhanced, rename the old _internal folder to _internal.previous
   (use another unused name if it already exists). Copy the new LANStream.exe
   and the complete new _internal folder from the extracted LANStream-Enhanced
   folder into C:\LANStream-Enhanced. Replace the old LANStream.exe when asked.
   Do not combine the contents of the old and new _internal folders.
   You can also copy BUILD-IDENTITY.json and the included documentation.
5. Keep the installed config.txt, cache folder, ffmpeg.exe, ffprobe.exe and
   existing 7-Zip files in place. Do not copy an old diagnostic launcher into
   the installation. Start C:\LANStream-Enhanced\LANStream.exe normally.
6. Open the installed Enhanced app on port 8061 and sign in again if requested.
   Check Normal and Low Bandwidth playback, sound and your Favorites.

If your Enhanced installation is in a different folder, substitute that folder
above. The port comes from your existing configuration; this release does not
force the diagnostic port or a particular transcoder preference.

Rollback: close the new Enhanced window, retain its folder for diagnosis, and
restore the complete dated backup to the original installation path. Launch
LANStream.exe from that restored folder. Do not delete the backup before the
new build has been checked. Never overwrite the separate original C:\LANStream
installation or its configuration/cache.
