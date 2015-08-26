--- src/System/KQueue.chs.intermediate	2015-08-26 16:27:31 UTC
+++ src/System/KQueue.chs
@@ -131,7 +131,7 @@ enum FFlag
   , NoteExit   = NOTE_EXIT
   , NoteFork   = NOTE_FORK
   , NoteExec   = NOTE_EXEC
-#ifndef __FreeBSD__
+#if ! defined __FreeBSD__ && ! defined __DragonFly__
   , NoteSignal = NOTE_SIGNAL
   , NoteReap   = NOTE_REAP
 #endif
