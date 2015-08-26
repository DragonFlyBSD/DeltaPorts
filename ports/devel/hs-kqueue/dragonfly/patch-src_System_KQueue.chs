--- src/System/KQueue.chs.intermediate	2015-08-26 16:27:31 UTC
+++ src/System/KQueue.chs
@@ -106,7 +106,7 @@ enum Flag
 // Not on Mac OS X
 //  , EvDispatch = EV_DISPATCH
   , EvDelete   = EV_DELETE
-  , EvReceipt  = EV_RECEIPT
+//  , EvReceipt  = EV_RECEIPT
   , EvOneshot  = EV_ONESHOT
   , EvClear    = EV_CLEAR
   , EvEof      = EV_EOF
@@ -131,7 +131,7 @@ enum FFlag
   , NoteExit   = NOTE_EXIT
   , NoteFork   = NOTE_FORK
   , NoteExec   = NOTE_EXEC
-#ifndef __FreeBSD__
+#if ! defined __FreeBSD__ && ! defined __DragonFly__
   , NoteSignal = NOTE_SIGNAL
   , NoteReap   = NOTE_REAP
 #endif
