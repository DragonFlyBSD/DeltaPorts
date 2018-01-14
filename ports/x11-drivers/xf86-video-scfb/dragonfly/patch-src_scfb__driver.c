--- src/scfb_driver.c.intermediate	2018-01-14 18:54:13 UTC
+++ src/scfb_driver.c
@@ -112,6 +112,7 @@
 static Bool ScfbSwitchMode(SWITCH_MODE_ARGS_DECL);
 static int ScfbValidMode(SCRN_ARG_TYPE, DisplayModePtr, Bool, int);
 static void ScfbLoadPalette(ScrnInfoPtr, int, int *, LOCO *, VisualPtr);
+static void ScfbDPMSSet(ScrnInfoPtr, int, int);
 static Bool ScfbSaveScreen(ScreenPtr, int);
 static void ScfbSave(ScrnInfoPtr);
 static void ScfbRestore(ScrnInfoPtr);
@@ -201,7 +202,7 @@
 	/* Check that we're being loaded on a OpenBSD or NetBSD system. */
 	LoaderGetOS(&osname, NULL, NULL, NULL);
 	if (!osname || (strcmp(osname, "freebsd") != 0 && strcmp(osname, "openbsd") != 0 &&
-	                strcmp(osname, "netbsd") != 0)) {
+	                strcmp(osname, "netbsd") != 0 && strcmp(osname, "dragonfly") != 0)) {
 		if (errmaj)
 			*errmaj = LDR_BADOS;
 		if (errmin)
@@ -800,6 +801,10 @@
 		return FALSE;
 	}
 
+	/* Init DPMS */
+	xf86DrvMsg(pScrn->scrnIndex, X_INFO, "Initializing DPMS\n");
+	xf86DPMSInit(pScreen, ScfbDPMSSet, 0);
+
 #ifdef XFreeXDGA
 	if (!fPtr->rotate)
 		ScfbDGAInit(pScrn, pScreen);
@@ -999,6 +1004,31 @@
 	/* TODO */
 }
 
+static void
+ScfbDPMS(ScrnInfoPtr pScrn, int state)
+{
+	ScfbPtr fPtr = SCFBPTR(pScrn);
+
+	ioctl(fPtr->fd, FBIO_BLANK, &state);
+}
+
+static void
+ScfbDPMSSet(ScrnInfoPtr pScrn, int PowerManagementMode, int flags)
+{
+	xf86DrvMsgVerb(pScrn->scrnIndex, X_INFO, DEFAULT_LOG_VERBOSE,
+		       "ScfbDPMSSet(%d, %d)\n",
+		       PowerManagementMode, flags);
+
+	if (!pScrn->vtSema)
+		return;
+
+	/* The mapping of DPMSMode* to V_DISPLAY_* values is unclear. */
+	if (PowerManagementMode == DPMSModeOn)
+		ScfbDPMS(pScrn, V_DISPLAY_ON);
+	else
+		ScfbDPMS(pScrn, V_DISPLAY_SUSPEND);
+}
+
 static Bool
 ScfbSaveScreen(ScreenPtr pScreen, int mode)
 {
