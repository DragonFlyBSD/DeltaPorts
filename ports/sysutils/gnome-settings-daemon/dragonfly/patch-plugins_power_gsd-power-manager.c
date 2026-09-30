--- plugins/power/gsd-power-manager.c.orig
+++ plugins/power/gsd-power-manager.c
@@ -2387,8 +2387,16 @@
                  * and its fade has finished.
                  *
                  * See also idle_configure() */
+#ifndef __DragonFly__
                 if (active)
                         idle_set_mode (manager, GSD_POWER_IDLE_MODE_BLANK);
+#else
+                /* DragonFly: do not blank the instant the lock screen
+                 * activates; idle_configure() already armed the idle watches,
+                 * so blanking still happens once the idle-delay timeout
+                 * elapses -- a grace period, not an instant blackout. */
+                (void) active;
+#endif
         }
 }
 
