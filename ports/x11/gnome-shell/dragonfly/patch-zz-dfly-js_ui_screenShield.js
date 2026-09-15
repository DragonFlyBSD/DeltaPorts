--- js/ui/screenShield.js.orig
+++ js/ui/screenShield.js
@@ -600,7 +600,11 @@
 
         this._resetLockScreen({
             animateLockScreen: animate,
-            fadeToBlack: true,
+            // DragonFly: keep the lock screen (clock) visible on lock instead
+            // of fading straight to black. The screen still goes black when the
+            // session later goes idle (org.gnome.desktop.session idle-delay),
+            // so there is a grace period rather than an immediate blackout.
+            fadeToBlack: false,
         });
         // We used to set isActive and emit active-changed here,
         // but now we do that from lockScreenShown, which means
