--- js/gdm/util.js.orig
+++ js/gdm/util.js
@@ -8,6 +8,7 @@
 import * as OVirt from './oVirt.js';
 import * as Vmware from './vmware.js';
 import * as Main from '../ui/main.js';
+import * as LoginManager from '../misc/loginManager.js';
 import {logErrorUnlessCancelled} from '../misc/errorUtils.js';
 import {loadInterfaceXML} from '../misc/fileUtils.js';
 import * as Params from '../misc/params.js';
@@ -820,6 +821,18 @@
         if (isCredentialManager && isForeground) {
             this._credentialManagers[serviceName].token = null;
             this._preemptingService = null;
+        }
+
+        // DragonFly: no logind to broadcast the 'Unlock' signal that
+        // screenShield waits on to leave the lock screen. When a reauth-only
+        // verification (screen unlock) succeeds, fire 'Unlock' on the dummy
+        // session proxy directly so the shield deactivates. On logind the
+        // session proxy has no emitUnlock(), so this is a no-op there and the
+        // real logind signal drives unlock as before.
+        if (this._reauthOnly) {
+            LoginManager.getLoginManager().getCurrentSessionProxy().then(session => {
+                session?.emitUnlock?.();
+            }).catch(() => {});
         }
 
         this.emit('verification-complete');
