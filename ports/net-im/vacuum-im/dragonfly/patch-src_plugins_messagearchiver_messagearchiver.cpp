c++11 compat

--- src/plugins/messagearchiver/messagearchiver.cpp.orig	2015-06-08 18:32:43.000000000 +0000
+++ src/plugins/messagearchiver/messagearchiver.cpp
@@ -9,8 +9,8 @@
 #define SESSIONS_FILE_NAME    "sessions.xml"
 
 #define SHC_MESSAGE_BODY      "/message/body"
-#define SHC_PREFS             "/iq[@type='set']/pref[@xmlns="NS_ARCHIVE"]"
-#define SHC_PREFS_OLD         "/iq[@type='set']/pref[@xmlns="NS_ARCHIVE_OLD"]"
+#define SHC_PREFS             "/iq[@type='set']/pref[@xmlns=" NS_ARCHIVE "]"
+#define SHC_PREFS_OLD         "/iq[@type='set']/pref[@xmlns=" NS_ARCHIVE_OLD "]"
 
 #define ADR_STREAM_JID        Action::DR_StreamJid
 #define ADR_CONTACT_JID       Action::DR_Parametr1
