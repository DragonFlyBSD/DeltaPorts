Disable _rl_enable_bracketed_paste in case of cons25 too.
Avoids echoing 2004h.

--- terminal.c.orig	2025-05-14 16:44:40 UTC
+++ terminal.c
@@ -580,7 +580,7 @@ _rl_init_terminal_io (const char *termin
 
   _rl_term_isansi = RL_ANSI_TERM_DEFAULT;
-  dumbterm = STREQ (term, "dumb") || STREQ (term, "vt52") || STREQ (term, "adm3a");
++  dumbterm = STREQ (term, "dumb") || STREQ (term, "vt52") || STREQ (term, "adm3a") || STREQ (term, "cons25");
   if (dumbterm)
     _rl_term_isansi = 0;
 
