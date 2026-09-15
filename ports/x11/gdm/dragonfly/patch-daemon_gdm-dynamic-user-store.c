--- daemon/gdm-dynamic-user-store.c.orig
+++ daemon/gdm-dynamic-user-store.c
@@ -20,7 +20,9 @@
 #include "config.h"
 
 #include <grp.h>
+#ifdef __linux__
 #include <shadow.h>
+#endif
 #include <sys/types.h>
 #ifdef HAVE_USERDB
 #include <systemd/sd-varlink.h>
@@ -165,10 +167,16 @@
 static PwdLock
 lock_pwd_db ()
 {
+#ifdef __linux__
         PwdLock lock = lckpwdf () >= 0;
         if (!lock)
                 g_warning ("Failed to lock passwd database, ignoring: %m");
         return lock;
+#else
+        /* No lckpwdf(3) outside glibc; dynamic users are never created on
+         * this platform (have_userdb is false), so report "not locked". */
+        return FALSE;
+#endif
 }
 
 static void
@@ -177,8 +185,10 @@
         if (!lock)
                 return;
 
+#ifdef __linux__
         if (ulckpwdf () < 0)
                 g_warning ("Failed to unlock passwd database, ignoring: %m");
+#endif
 }
 
 G_DEFINE_AUTO_CLEANUP_FREE_FUNC (PwdLock, unlock_pwd_db, FALSE)
@@ -300,6 +310,24 @@
         struct group *grp;
         g_autofree char *home = NULL;
         DynamicUser *user;
+
+#ifndef HAVE_USERDB
+        /* Preallocated static users only: a lingering store entry for the
+         * preferred name can only be a stale record of a dead greeter
+         * (single static seat, no dynamic uid range). Drop it so the name
+         * and uid are reused -- otherwise pick_username walks the
+         * nonexistent gdm-greeter-2..N range and hits its assertion. */
+        {
+                DynamicUser *stale;
+
+                stale = g_hash_table_lookup (store->by_name, preferred_username);
+                if (stale != NULL) {
+                        g_debug ("GdmDynUserStore: dropping stale entry for '%s'",
+                                 preferred_username);
+                        gdm_dynamic_user_store_remove (store, stale->uid);
+                }
+        }
+#endif
 
         username = pick_username (store, preferred_username);
 
