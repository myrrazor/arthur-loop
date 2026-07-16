Output docs/assets/arthur-loop-demo.gif

Set FontSize 18
Set Width 1100
Set Height 680
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Padding 24

Hide
Type "mkdir -p docs/assets/demo-instance"
Enter
Type "arthur init --root docs/assets/demo-instance --yes --main-agent none --advisor manual --executor manual --tracker none --no-governor --demo --force"
Enter
Type "clear"
Enter
Show

Type "arthur status --root docs/assets/demo-instance"
Enter
Sleep 5s

Type "arthur tick --root docs/assets/demo-instance --dry-run --format markdown"
Enter
Sleep 5s
