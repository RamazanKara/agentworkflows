# README recording: node scripts/compose-demo.mjs
# Requires vhs, ttyd, ffmpeg, make, installed console dependencies/Chromium, and make compose-up.
# Records real output; the middle of the longer recovery checks is cut for readability.
Output .out/compose-demo/terminal.mp4

Set Shell "bash"
Set FontSize 16
Set Width 1100
Set Height 760
Set Theme "Catppuccin Mocha"
Set Padding 14
Set TypingSpeed 45ms
Set WindowBar Colorful
Set Framerate 12

Hide
Type `cd "$AGENTWORKFLOWS_DEMO_ROOT" && clear`
Enter
Sleep 500ms
Show

Type "make compose-smoke | tee .out/compose-demo/smoke.log; clear; tail -n 12 .out/compose-demo/smoke.log"
Sleep 400ms
Enter
Sleep 8s
Hide
Wait+Screen@180s /All checks passed/
Show
Sleep 3s
