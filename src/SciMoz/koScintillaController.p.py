# Copyright (c) 2000-2006 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

from xpcom import components, COMException
from xpcom.client import WeakReference
import string
import re
import logging
import eollib

from zope.cachedescriptors.property import Lazy as LazyProperty

log = logging.getLogger("koScintillaController")
#log.setLevel(logging.DEBUG)

command_map = {
    'cmd_selectHome' : 'vCHomeWrapExtend',
    'cmd_selectEnd' : 'lineEndWrapExtend',
    'cmd_selectCharPrevious' : 'charLeftExtend',
    'cmd_selectCharNext' : 'charRightExtend',
    'cmd_pageUp' : 'pageUp',
    'cmd_pageDown' : 'pageDown',
    'cmd_selectPageUp' : 'pageUpExtend',
    'cmd_selectPageDown' : 'pageDownExtend',
    'cmd_selectLinePrevious' : 'lineUpExtend',
    'cmd_selectLineNext' : 'lineDownExtend',
    'cmd_selectRectCharPrevious' : 'charLeftRectExtend',
    'cmd_selectRectCharNext' : 'charRightRectExtend',
    'cmd_selectRectLinePrevious' : 'lineUpRectExtend',
    'cmd_selectRectLineNext' : 'lineDownRectExtend',
    'cmd_selectRectHome' : 'vCHomeRectExtend',
    'cmd_selectRectEnd' : 'lineEndRectExtend',
    'cmd_selectRectPageUp' : 'pageUpRectExtend',
    'cmd_selectRectPageDown' : 'pageDownRectExtend',
    'cmd_undo' : 'undo',
    'cmd_redo' : 'redo',
    'cmd_cut' : 'cut',
    'cmd_copy' : 'copy',
    'cmd_paste' : 'paste',
    'cmd_selectWordLeft' : 'wordLeftExtend',
    'cmd_selectWordRight' : 'wordRightExtend',
    'cmd_wordPartLeftExtend' : 'wordPartLeftExtend',
    'cmd_wordPartRightExtend' : 'wordPartRightExtend',
    'cmd_deleteWordLeft' : 'delWordLeft',
    'cmd_deleteWordRight' : 'delWordRight',
    'cmd_documentHome' : 'documentStart',
    'cmd_documentEnd' : 'documentEnd',
    'cmd_selectDocumentHome' : 'documentStartExtend',
    'cmd_selectDocumentEnd' : 'documentEndExtend',
    'cmd_editSelectAll' : 'selectAll', # backwards compat for custom bindings
    'cmd_selectAll' : 'selectAll',
    'cmd_delete' : 'clear',
    'cmd_back' : 'deleteBack',
    'cmd_lineScrollUp' : 'lineScrollUp',
    'cmd_lineScrollDown' : 'lineScrollDown',
    'cmd_lineCut' : 'lineCut',
    'cmd_lineDelete' : 'lineDelete',
#    'cmd_lineTranspose' : 'lineTranspose',  # commented out because its undo behavior is wrong.
    'cmd_fontZoomIn' : 'zoomIn',
    'cmd_fontZoomOut' : 'zoomOut',
    'cmd_toggleOvertype' : 'editToggleOvertype',
    #'cmd_newline' : 'newLine',
    'cmd_paraUp' : 'paraUp',
    'cmd_paraDown' : 'paraDown',
    'cmd_copyLine' : 'lineCopy',
    'cmd_homeAbsolute' : 'home',
}

class ClipboardWrapper():
    def __init__(self):
        self.clipboard = components.classes["@mozilla.org/widget/clipboard;1"].getService(components.interfaces.nsIClipboard)
        self.transferable = components.classes["@mozilla.org/widget/transferable;1"].createInstance(components.interfaces.nsITransferable)
        self.transferable.addDataFlavor("text/unicode")
        
    def _getTextFromClipboard(self):
        self.clipboard.getData(self.transferable, self.clipboard.kGlobalClipboard)
        try:
            (str, strLength) = self.transferable.getTransferData("text/unicode")
            return str.QueryInterface(components.interfaces.nsISupportsString).data[:strLength/2]
        except COMException:
            log.error("ClipboardWrapper._getTextFromClipboard: Nothing on the clipboard to get?")
            return ""
        except:
            log.exception("_getTextFromClipboard: unknown")
            raise

class koScintillaController:
    _com_interfaces_ = components.interfaces.ISciMozController
    _reg_clsid_ = "{726cc885-6d17-48af-b8a6-c9b759f1fe6b}"
    _reg_contractid_ = "@ActiveState.com/scintilla/controller;1"

    def init(self, scimoz):
        self.scimoz = WeakReference(scimoz)
        self._lastcutposition = None
        # A cached collection of boolean attributes.
        # No key == "dunno if boolean"
        # Key == None == definitely not bool.
        # Else - key == scimoz attribute name.
        self.bool_attributes = {}
        self._loc_saving_cmds = ['cmd_documentHome', 'cmd_documentEnd']

    @LazyProperty
    def _koHistorySvc(self):
        return components.classes["@activestate.com/koHistoryService;1"].\
                        getService(components.interfaces.koIHistoryService)
    @LazyProperty
    def _koSysUtils(self):
        return components.classes["@activestate.com/koSysUtils;1"].\
                            getService(components.interfaces.koISysUtils)
    @LazyProperty
    def _koPrefs(self):
        return components.classes["@activestate.com/koPrefService;1"].\
                            getService(components.interfaces.koIPrefService).prefs

    def test_scimoz(self, scimoz):
        self.init(scimoz)
        ScintillaControllerTestCase.controller = self
        testCases = [ScintillaControllerTestCase]
        sciutils.runSciMozTests(testCases, scimoz)

    def isCommandEnabled( self, command_name ):
        # Result: boolean
        # In: param0: wstring
        meth = getattr(self, "_is_%s_enabled" % (str(command_name),), None)
        if meth is None:
            # Handle the fact it may be a bool property that can be toggled.
            # (and cache the fact, to speed things up 2nd and later times)
            attr = self.bool_attributes.get(command_name, 0)
            if attr == 0: # not in map
                attr = prop_name = command_map.get(command_name)
                if attr is not None:
                    sm = self.scimoz()
                    attr = getattr(sm, attr)
                    if type(attr) == type(0):
                        # Cache for next time.
                        self.bool_attributes[command_name] = prop_name
                        # And to avoid re-fetching, get out here!
                        return attr
                    # Not an attribute we can use - that is OK.
                    attr = None

            if attr is None:
                # No custom function, and no scintilla integer property of that name.
                # Assume OK.
                rc = 1
            else:
                # We have a remembered property name - use it.
                sm = self.scimoz()
                rc = getattr(sm, attr)
        else:
            # Custom function - do it
            rc = meth()
        return rc

    def supportsCommand( self, command_name ):
        # Result: boolean
        # In: param0: wstring

        return command_name in command_map or hasattr(self, '_do_'+command_name)

    def doCommand( self, command_name ):
        # Result: void - None
        # In: param0: wstring
        sm = self.scimoz()
        old_sel_exists = sm.selectionEnd != sm.selectionStart
        currentPos = sm.currentPos
        if command_name == 'cmd_undo':
            if sm.autoCActive():
                sm.autoCCancel()
            elif sm.callTipActive():
                sm.callTipCancel()
            targetPos = None
            if self._lastcutposition is not None and sm.currentPos == self._lastcutposition:
                # note where we want to be post undo
                targetPos = self._lastcutposition
            sm.undo()
            if targetPos:
                sm.currentPos = sm.anchor = targetPos
            return
        if command_name == 'cmd_copy':
            # If there is a selection, we just do the usual cut
            if sm.selectionStart != sm.selectionEnd:
                sm.copy()
                sm.sendUpdateCommands("select")
                sm.sendUpdateCommands("clipboard")
                return
            elif not self._koPrefs.getBooleanPref('editSmartCutCopyWithoutSelection'):
                return
            # if there's no selection, we copy the current line, being careful to leave
            # the cursor in its original position.
            # but should we?  Most of the time one will want to place the line somewhere else.
            # Hmm -- I think I'll leave the cursor at the beginning of the copied line for now.
            oldCurrentPos = sm.currentPos
            lineStart = sm.lineFromPosition(sm.currentPos)
            lineStartPos = sm.positionFromLine(lineStart)
            nextLineStartPos = sm.positionFromLine(lineStart + 1)
            sm.selectionStart = lineStartPos
            if sm.getLineEndPosition(lineStart) == nextLineStartPos:
                # At last line of doc, buffer doesn't end with an EOL
                line = sm.getTextRange(lineStartPos, nextLineStartPos)
                eol = eollib.eol2eolStr[eollib.scimozEOL2eol[sm.eOLMode]]
                finalLine = line + eol
                finalLineLength = self._koSysUtils.byteLength(finalLine)
                sm.copyText(finalLineLength, finalLine)
            else:
                sm.selectionEnd = nextLineStartPos
                sm.copy()
            sm.sendUpdateCommands("select")
            sm.sendUpdateCommands("clipboard")
            sm.currentPos = sm.selectionEnd = sm.selectionStart
            sm.chooseCaretX()
            self._lastcutposition = None 
        elif command_name == 'cmd_cut':
            # If there is a selection, we just do the usual cut
            if sm.selectionStart != sm.selectionEnd:
                #if sm.lineFromPosition(sm.selectionStart) < sm.lineFromPosition(sm.selectionEnd):
                #    self._koHistorySvc.note_curr_editor_loc(None)
                sm.cut()
                self._lastcutposition = None
                sm.sendUpdateCommands("clipboard")
                return
            elif not self._koPrefs.getBooleanPref('editSmartCutCopyWithoutSelection'):
                return
            # Do nothing at end of file except if there's stuff to the left
            if sm.currentPos == sm.textLength and \
               sm.getColumn(sm.currentPos) == 0: return 
            # if there's no selection, we get to do our fancy cut.
            # If the last cut position was different that the current position, this is the 'first cut' -- e.g. a lineCut
            # We're cutting a line -- either the first, or possibly a subsequent one
            lineNo = sm.lineFromPosition(sm.currentPos)
            lineStart = sm.positionFromLine(lineNo)
            nextLineStartPos = sm.positionFromLine(lineNo + 1)
            sm.beginUndoAction()
            try:
                if sm.getLineEndPosition(lineNo) == nextLineStartPos:
                    # At last line of doc, buffer doesn't end with an EOL
                    # Unlike copy, here we can append a newline
                    eol = eollib.eol2eolStr[eollib.scimozEOL2eol[sm.eOLMode]]
                    sm.insertText(sm.length, eol)
                    nextLineStartPos += len(eol)
                self._doSmartCut(lineStart, nextLineStartPos)
            finally:
                sm.endUndoAction()
            return
        elif command_name == 'cmd_lineDelete':
            # If there is no selection, we just do the usual line-delete
            if sm.selectionStart == sm.selectionEnd:
                sm.lineDelete()
                return
            startLineNum = sm.lineFromPosition(sm.selectionStart)
            finalStartPos = sm.positionFromLine(startLineNum)
            endLineNum = sm.lineFromPosition(sm.selectionEnd)
            if startLineNum == endLineNum:
                sm.lineDelete()
                return
            if endLineNum < sm.lineCount - 1:
                finalEndPos = sm.positionFromLine(endLineNum + 1)
            else:
                finalEndPos = sm.textLength
            # Now delete all lines, including EOL of last line,
            # remove all markers, and set selection at point
            # after the end of the next line.
            # Note that breakpoints aren't removed from the breakpoints tab.
            # Need to send a notification to make that happen.
            sm.beginUndoAction()
            try:
                for lineNum in range(startLineNum, endLineNum + 1):
                    markerMask = sm.markerGet(lineNum)
                    i = 0
                    while markerMask:
                        if markerMask & 1:
                            sm.markerDelete(lineNum, i)
                            markerMask &= ~1
                        markerMask >>= 1
                        i += 1
                sm.targetStart = finalStartPos
                sm.targetEnd = finalEndPos
                sm.replaceTarget(0, "")
                sm.currentPos = sm.anchor = finalStartPos
            finally:
                sm.endUndoAction()
            return
        methname= '_do_'+command_name
        attr = getattr(self, methname, None)
        if attr is None:
            attr = getattr(sm, command_map[command_name])
        # If we fetch an attribute and it is a number..
        if type(attr)==type(0): # Assume boolean
            setattr(sm, command_map[command_name], not attr)
        # Usually it will be a method..
        elif callable(attr):
            if command_name in self._loc_saving_cmds:
                self._koHistorySvc.note_curr_editor_loc(None)
            #elif (command_name == "cmd_delete"
            #      and sm.selectionStart != sm.selectionEnd
            #      and sm.lineFromPosition(sm.selectionStart) < sm.lineFromPosition(sm.selectionEnd)):
            #    self._koHistorySvc.note_curr_editor_loc(None)
            attr()
        # and hopefully this will never happen!
        else:
            raise TypeError, "The command map entry '%s' yielded a '%r' - dunno what to do with it" % (command_name, attr)
        # We only need to send a command update when the selection changes _shape_, not location
        # (ie, only when it changes from no selection to selection, or vice-versa.
        if command_name not in ('cmd_redo', 'cmd_killLine'):
            # We've done something else, that means that we're not in a multiple-ctrl-x mode anymore
            self._lastcutposition = None 
        new_sel_exists = sm.selectionEnd != sm.selectionStart
        if old_sel_exists != new_sel_exists:
            sm.sendUpdateCommands("select")

    def _doSmartCut(self, start, end):
        sm = self.scimoz()
        sm.targetStart = start
        sm.targetEnd = end
        line = sm.getTextRange(start, end)
        sm.beginUndoAction()
        try:
            sm.replaceTarget(0, '')
            if self._lastcutposition != sm.currentPos:
                # first cut -- lineCut
                self._cutbuffer = line
                self._lastcutposition = sm.currentPos
            else:
                # If we're here, it's because someone did Cut twice w/o a
                # selection -- we need to accumulate the lines into the cut
                # buffer
                self._cutbuffer += line
            decoded = self._cutbuffer.encode('utf-8')
            byteLen = len(decoded)
            #print "cut copyText %d %r" % (byteLen, decoded)
            # Editor::CopyText takes utf-8-length, unicode-chars
            sm.copyText(byteLen, self._cutbuffer)
            sm.chooseCaretX()
        finally:
            sm.endUndoAction()
        sm.sendUpdateCommands("clipboard")

    def onEvent( self, param0 ):
        # Result: void - None
        # In: param0: wstring
        log.warn("Scintilla controller caught an event: ", param0)

    def _has_sel(self):
        sm = self.scimoz()
        return sm.selectionEnd != sm.selectionStart
    # Specific command handlers
    def _is_cmd_undo_enabled(self):
        return self.scimoz().canUndo()
    def _is_cmd_redo_enabled(self):
        return self.scimoz().canRedo()
    def _is_cmd_paste_enabled(self):
        return self.scimoz().canPaste()
    def _is_cmd_cut_enabled(self):
        return not self.scimoz().readOnly
    def _is_cmd_copy_enabled(self):
        return 1
    def _is_cmd_delete_enabled(self):
        return 1

    def _is_cmd_pasteAndSelect_enabled(self):
        return self.scimoz().canPaste()
    def _do_cmd_pasteAndSelect(self):
        sm = self.scimoz()
        start = sm.selectionStart
        sm.paste()
        sm.anchor = start

    def _is_cmd_tabAwarePaste_enabled(self):
        return self.scimoz().canPaste()

    _wsRE = re.compile(r'(\s+)')
    def _do_cmd_tabAwarePaste(self):
        scimoz = self.scimoz()
        text = self._getClipboardText()
        if len(text) == 0:
            # Nothing to do
            return
        eol = eollib.eol2eolStr[eollib.scimozEOL2eol[scimoz.eOLMode]]
        lines = text.splitlines()
        currentPos = scimoz.currentPos
        currentLineNo = scimoz.lineFromPosition(currentPos)
        lineStartPos = scimoz.positionFromLine(currentLineNo)
        leadingText = scimoz.getTextRange(lineStartPos, currentPos)
        if len(leadingText) == 0:
            scimoz.paste()
        else:
            m = self._wsRE.match(leadingText)
            if not m:
                scimoz.paste()
            else:
                leadingWS = m.group(1)
                # Find the leading white-space in this block:
                # use the first line if it has it, otherwise the second,
                # but set to empty string if not all remaining lines start
                # with that first line's leading white-space.
                if lines[0] and lines[0][0] in " \t":
                    leading_ws_m = self._wsRE.match(lines[0])
                    contLine = 1
                elif len(lines) > 1:
                    leading_ws_m = self._wsRE.match(lines[1])
                    contLine = 2
                else:
                    leading_ws_m = None
                fixedLines = None
                if leading_ws_m:
                    initWS = leading_ws_m.group(1)
                    if all([x.startswith(initWS) for x in lines[contLine:]]):
                        prefixLen = len(initWS)
                        if contLine == 2:
                            fixedLines = [lines[0]]
                        else:
                            fixedLines = [lines[0][prefixLen:]]
                        fixedLines += [leadingWS + line[prefixLen:]
                                       for line in lines[1:]]
                    else:
                        # Do nothing -- the white-space in the text we
                        # copied is irregular, so preserve it
                        fixedLines = lines
                else:
                    # The copied block has no predictable white-space, 
                    # so just add the target WS to it, but don't take any
                    # of the source white-space off.
                    fixedLines = ([lines[0]]
                                  + [leadingWS + line for line in lines[1:]])
                fixedText = eol.join(fixedLines)
                scimoz.insertText(currentPos, fixedText)
        scimoz.anchor = scimoz.currentPos = currentPos

    def _getClipboardText(self):
        return str(ClipboardWrapper()._getTextFromClipboard())

    def _do_cmd_endOfWord(self):
        self.scimoz().wordRightEnd()
        return 1

    def _do_cmd_endOfWordExtend(self):
        self.scimoz().wordRightEndExtend()
        return 1

    def _do_cmd_beginningOfWord(self):
        self.scimoz().wordLeftEnd()
        return 1

    def _do_cmd_beginningOfWordExtend(self):
        self.scimoz().wordLeftEndExtend()
        return 1

    def _do_cmd_selectWordUnderCursor(self):
        sm = self.scimoz()
        pos = sm.currentPos
        word_start = sm.wordStartPosition(pos, True)
        word_end = sm.wordEndPosition(pos, True)
        sm.anchor = word_start
        sm.currentPos = word_end

    def _do_cmd_lineTranspose(self):
        self.scimoz().lineTranspose();
        return 1
    
    def _do_cmd_join(self):
        sm = self.scimoz()
        sm.beginUndoAction()
        try:
            lineNo = sm.lineFromPosition(sm.currentPos)
            lineStart = sm.positionFromLine(lineNo)
            lineEnd = sm.getLineEndPosition(lineNo)
            whitespaceToLeft = False
            if sm.getTextRange(sm.currentPos, lineEnd).strip():
                # We're in the middle of a line -- assume we're at the
                # end of the line.
                sm.lineEnd()
            if sm.getWCharAt(sm.currentPos-1) == ' ':
                whitespaceToLeft = True
            sm.lineEnd()
            # delete all but last whitespace characters to the left
            # of cursor
            start = end = sm.currentPos
            endBuffer = sm.textLength
            lineStart = sm.positionFromLine(sm.lineFromPosition(sm.currentPos))
            # if there is non-whitespace to the left, then delete any
            # immediately preceding whitespace
            stuffToLeft = sm.getTextRange(lineStart, sm.currentPos)
            if stuffToLeft.strip():
                while start >= lineStart+1 and sm.getWCharAt(start-1) == ' ':
                    start -= 1
                replacement = ' '
            else:
                replacement = ''
            while end <= endBuffer and sm.getWCharAt(end) in ' \r\n':
                end += 1
            sm.targetStart = start
            sm.targetEnd = end
            sm.replaceTarget(len(replacement), replacement)
            if whitespaceToLeft:
                sm.gotoPos(sm.targetEnd)
            else:
                sm.gotoPos(sm.targetStart)
        finally:
            sm.endUndoAction()

    def _do_cmd_linePrevious(self):
        self.scimoz().lineUp()
        
    def _do_cmd_lineNext(self):
        self.scimoz().lineDown()
        
    def _do_cmd_left(self):
        self.scimoz().charLeft()

    def _do_cmd_right(self):
        self.scimoz().charRight()

    def _do_cmd_wordLeft(self):
        self.scimoz().wordLeft()

    def _do_cmd_wordLeftSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        self._do_cmd_wordLeft()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.positionFromLine(line):
            self._do_cmd_wordRight()
            sm.home()

    def _do_cmd_selectWordLeftSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        sm.wordLeftExtend()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.positionFromLine(line):
            sm.wordRightExtend()
            sm.homeExtend()

    def _do_cmd_wordRight(self):
        self.scimoz().wordRight()

    def _do_cmd_wordRightSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        self._do_cmd_wordRight()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.getLineEndPosition(line):
            self._do_cmd_wordLeft()
            sm.lineEnd()

    def _do_cmd_selectWordRightSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        sm.wordRightExtend()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.getLineEndPosition(line):
            sm.wordLeftExtend()
            sm.lineEndExtend()
        
    def _do_cmd_wordPartLeft(self):
        self.scimoz().wordPartLeft()

    def _do_cmd_wordPartLeftSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        self._do_cmd_wordPartLeft()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.positionFromLine(line):
            self._do_cmd_wordPartRight()
            sm.home()

    def _do_cmd_wordPartLeftExtendSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        sm.wordPartLeftExtend()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.positionFromLine(line):
            sm.wordPartRightExtend()
            sm.homeExtend()
        
    def _do_cmd_wordPartRight(self):
        self.scimoz().wordPartRight()

    def _do_cmd_wordPartRightSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        self._do_cmd_wordPartRight()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.getLineEndPosition(line):
            self._do_cmd_wordPartLeft()
            sm.lineEnd()

    def _do_cmd_wordPartRightExtendSameLine(self):
        sm = self.scimoz()
        pos = sm.currentPos
        line = sm.lineFromPosition(pos)
        sm.wordPartRightExtend()
        if line != sm.lineFromPosition(sm.currentPos) and pos != sm.getLineEndPosition(line):
            sm.wordPartLeftExtend()
            sm.lineEndExtend()

    def _do_cmd_wordLeftEnd(self):
        self.scimoz().wordLeftEnd()

    def _do_cmd_wordRightEnd(self):
        self.scimoz().wordRightEnd()
        
    def _do_cmd_pasteYankedLinesBefore(self):
        self._do_cmd_pasteYankedLines(pasteAfter=0)

    def _do_cmd_pasteYankedLinesAfter(self):
        self._do_cmd_pasteYankedLines(pasteAfter=1)

    def _do_cmd_pasteYankedLines(self, pasteAfter=1):
        sm = self.scimoz()
        sm.beginUndoAction()
        try:
            # Got to column 0 on current line
            if pasteAfter:
                lineNo = sm.lineFromPosition(sm.currentPos)
                if lineNo == sm.lineCount:
                    # Case where already on the last line (lineDown no help)
                    sm.newLine()
                else:
                    sm.lineDown()
            sm.home()
            start = sm.currentPos
            sm.paste()
            sm.anchor = sm.currentPos
            sm.currentPos = start
            sm.vCHomeWrap()
        finally:
            sm.endUndoAction()

    def _do_cmd_clearLine(self):
        sm = self.scimoz()
        sm.beginUndoAction()
        sm.delLineLeft()
        sm.delLineRight()
        sm.endUndoAction()

    def _do_cmd_clearLineHome(self):
        self.scimoz().delLineLeft()

    def _do_cmd_clearLineEnd(self):
        self.scimoz().delLineRight()

    def _do_cmd_cutChar(self):
        sm = self.scimoz()
        sm.beginUndoAction()
        try:
            curpos = sm.currentPos
            lineNo = sm.lineFromPosition(sm.currentPos)
            lineEndPos = sm.getLineEndPosition(lineNo)
            if curpos < lineEndPos:
                # delete character to the left of the cursor
                # XXX - Char or byte lengths?
                # copy the char first (so it can be pasted later if needed)
                sm.copyRange(curpos, curpos + 1)
                sm.chooseCaretX()
                sm.sendUpdateCommands("clipboard")
                # now remove the char
                sm.targetStart = curpos
                sm.targetEnd = curpos + 1
                sm.replaceTarget(0, "")
        finally:
            sm.endUndoAction()

    def _do_cmd_cutCharLeft(self):
        sm = self.scimoz()
        sm.beginUndoAction()
        try:
            curpos = sm.currentPos
            lineNo = sm.lineFromPosition(curpos)
            lineStartPos = sm.positionFromLine(lineNo)
            if curpos > lineStartPos:
                # delete character to the left of the cursor
                # XXX - Char or byte lengths?
                # copy the char first (so it can be pasted later if needed)
                sm.copyRange(curpos - 1, curpos)
                sm.chooseCaretX()
                sm.sendUpdateCommands("clipboard")
                # now remove the char
                sm.targetStart = curpos - 1
                sm.targetEnd = curpos
                sm.replaceTarget(0, "")
        finally:
            sm.endUndoAction()

    def _do_cmd_cutWordLeft(self):
        sm = self.scimoz()
        endPos = sm.currentPos
        sm.beginUndoAction()
        try:
            sm.wordLeft()
            startPos = sm.currentPos
            if endPos > startPos:
                # copy the word first (so it can be pasted later if needed)
                sm.copyRange(startPos, endPos)
                sm.chooseCaretX()
                sm.sendUpdateCommands("clipboard")
                # now remove the word
                sm.targetStart = startPos
                sm.targetEnd = endPos
                sm.replaceTarget(0, "")
        finally:
            sm.endUndoAction()

    def _do_cmd_cutWordRight(self):
        sm = self.scimoz()
        startPos = sm.currentPos
        sm.beginUndoAction()
        try:
            sm.wordRight()
            endPos = sm.currentPos
            if endPos > startPos:
                # copy the word first (so it can be pasted later if needed)
                sm.copyRange(startPos, endPos)
                sm.chooseCaretX()
                sm.sendUpdateCommands("clipboard")
                # now remove the word
                sm.targetStart = startPos
                sm.targetEnd = endPos
                sm.replaceTarget(0, "")
        finally:
            sm.endUndoAction()
            
    def _is_cmd_lineDuplicateUp_enabled(self):
        return self.scimoz().selections == 1

    def _do_cmd_lineDuplicateUp(self):
        sm = self.scimoz()
        if sm.selections > 1:
            return # do not duplicate multiple or rectangular selections

        sm.beginUndoAction()
        try:
            sm.selectionDuplicate()
            if not sm.selectionEmpty:
                if sm.lineFromPosition(sm.selectionStart) == sm.lineFromPosition(sm.selectionEnd):
                    # Duplicated an in-line selection to the right. Move it up
                    # onto its own line.
                    text = sm.selText
                    sm.deleteBack()
                    sm.home()
                    sm.addText(len(text), text)
                    sm.newLine()
                    sm.lineUp()
                    sm.home()
                    sm.lineEndExtend()
                elif sm.getColumn(sm.selectionEnd) != 0:
                    # Duplicated a multi-line selection to the right, but the
                    # first line is not on a new line. Put it on one.
                    start, end = sm.selectionStart, sm.selectionEnd
                    sm.gotoPos(end)
                    sm.newLine()
                    sm.setSel(start, end)
        finally:
            sm.endUndoAction()

    def _is_cmd_lineDuplicateDown_enabled(self):
        return self.scimoz().selections == 1

    def _do_cmd_lineDuplicateDown(self):
        sm = self.scimoz()
        if sm.selections > 1:
            return # do not duplicate multiple or rectangular selections

        sm.beginUndoAction()
        try:
            sm.selectionDuplicate()
            if not sm.selectionEmpty:
                if sm.lineFromPosition(sm.selectionStart) == sm.lineFromPosition(sm.selectionEnd):
                    # Duplicated an in-line selection to the right. Move it
                    # down onto its own line.
                    text = sm.selText
                    sm.deleteBack()
                    sm.lineEnd()
                    sm.newLine()
                    sm.addText(len(text), text)
                    sm.home()
                    sm.lineEndExtend()
                elif sm.getColumn(sm.selectionEnd) != 0:
                    # Duplicated a multi-line selection to the right, but the
                    # first line is not on a new line. Put it on one.
                    start, end = sm.selectionStart, sm.selectionEnd
                    length = end - start
                    sm.gotoPos(sm.selectionEnd)
                    sm.newLine()
                    offset = sm.currentPos - end # newline length
                    sm.setSel(start + length + offset, end + length + offset)
            else:
                sm.lineDown() # move to duplicated line
        finally:
            sm.endUndoAction()

    def _is_cmd_lineTransposeDown_enabled(self):
        return self.scimoz().selections == 1

    def _do_cmd_lineTransposeDown(self):
        sm = self.scimoz()
        if sm.selections > 1:
            return # do not transpose multiple or rectangular selections
        
        hasSelection = sm.selectionStart != sm.selectionEnd

        if not hasSelection:
            lineNo = sm.lineFromPosition(sm.currentPos)
            lineStart = sm.positionFromLine(lineNo)
            linePos = sm.currentPos - lineStart

        sm.beginUndoAction()
        try:
            sm.moveSelectedLinesDown()

            if not hasSelection:
                newLinePos = sm.positionFromLine(lineNo + 1)
                newPos = newLinePos + linePos
                sm.setSel(newPos, newPos)
        finally:
            sm.endUndoAction()

    def _is_cmd_lineTransposeUp_enabled(self):
        return self.scimoz().selections == 1

    def _do_cmd_lineTransposeUp(self):
        sm = self.scimoz()
        if sm.selections > 1:
            return # do not transpose multiple or rectangular selections
        
        hasSelection = sm.selectionStart != sm.selectionEnd

        if not hasSelection:
            lineNo = sm.lineFromPosition(sm.currentPos)
            lineStart = sm.positionFromLine(lineNo)
            linePos = sm.currentPos - lineStart

        sm.beginUndoAction()
        try:
            sm.moveSelectedLinesUp()

            if not hasSelection:
                newLinePos = sm.positionFromLine(lineNo - 1)
                newPos = newLinePos + linePos
                sm.setSel(newPos, newPos)
        finally:
            sm.endUndoAction()
        
    def _do_cmd_inlineSelectionDuplicateLeft(self):
        sm = self.scimoz()
        if sm.selections > 1 or sm.selectionEmpty or \
           sm.lineFromPosition(sm.selectionStart) != sm.lineFromPosition(sm.selectionEnd):
            return # only duplicate single-line selections
        sm.selectionDuplicate()
        
    def _do_cmd_inlineSelectionDuplicateRight(self):
        sm = self.scimoz()
        if sm.selections > 1 or sm.selectionEmpty or \
           sm.lineFromPosition(sm.selectionStart) != sm.lineFromPosition(sm.selectionEnd):
            return # only duplicate single-line selections
        anchor, pos = sm.anchor, sm.currentPos
        length = sm.selectionEnd - sm.selectionStart
        sm.selectionDuplicate()
        sm.setSel(anchor + length, pos + length)
        
    # Used by vim binding B
    def _do_cmd_wordLeftPastPunctuation(self):
        sm = self.scimoz()
        sm.currentPos = min(sm.currentPos, sm.anchor)
        lineno = sm.lineFromPosition(sm.currentPos)
        lineStartPos = sm.positionFromLine(lineno)
        line = sm.getTextRange(lineStartPos, sm.currentPos)
        matches = re.finditer("\\s+\\S", line)
        # XXX - Byte lengths?? Do we need to get character lengths??
        findPos = -1
        for m in matches:
            findPos = m.start()
        if findPos >= 0:
            sm.gotoPos(lineStartPos + findPos)
            sm.wordRight()
        elif lineno > 0:
            # Try the previous line, at worst we end at the start of the previous line
            lineno -= 1
            lineStartPos = sm.positionFromLine(lineno)
            lineEndPos = sm.getLineEndPosition(lineno)
            line = sm.getTextRange(lineStartPos, lineEndPos)
            matches = re.finditer("\\s+\\S", line)
            findPos = -1
            for m in matches:
                findPos = m.start()
            if findPos >= 0:
                sm.gotoPos(lineStartPos + findPos)
                sm.wordRight()
            else:
                sm.lineUp()
                sm.home()
        else:
            sm.home()

    # Used by vim binding W
    def _do_cmd_wordRightPastPunctuation(self):
        sm = self.scimoz()
        sm.currentPos = max(sm.currentPos, sm.anchor)
        if sm.currentPos >= sm.length:
            return
        lineno = sm.lineFromPosition(sm.currentPos)
        lineEndPos = sm.getLineEndPosition(lineno)
        # XXX - Byte lengths?? Do we need to get character lengths??
        nextPos = sm.positionAfter(sm.currentPos)
        if nextPos < lineEndPos:
            line = sm.getTextRange(nextPos, lineEndPos)
            searchMatch = re.search("\\s", line)
            if searchMatch:
                sm.gotoPos(sm.currentPos + searchMatch.start() + 1)
                sm.wordRight()
                return
        self._do_cmd_lineNextHome()

    # Used by vim binding E
    def _do_cmd_wordRightEndPastPunctuation(self):
        sm = self.scimoz()
        sm.currentPos = max(sm.currentPos, sm.anchor)
        lineno = sm.lineFromPosition(sm.currentPos)
        lineEndPos = sm.getLineEndPosition(lineno)
        if sm.currentPos == lineEndPos:
            sm.lineDown()
            sm.home()
            lineno = sm.lineFromPosition(sm.currentPos)
            lineEndPos = sm.getLineEndPosition(lineno)
        # XXX - Byte lengths?? Do we need to get character lengths??
        line = sm.getTextRange(sm.positionAfter(sm.currentPos), lineEndPos)
        searchMatch = re.search("\\S\\s", line)
        if searchMatch:
            sm.gotoPos(sm.currentPos + searchMatch.start() + 1)
        else:
            sm.lineEnd()

    # Utility function for finding text using regex's, searching backwards
    #  regexlist: list - regex's to search for
    #  direction: int - 1 is forwards, 0 is backwards
    #  getGroupPos: string - if set, the match position returned uses the offset
    #                        of this group, instead of the regex start position
    # Returns the position in the document which is the closest match, or None.
    def _find_closest_regex_backwards(self, regexlist, getGroupPos=None):
        sm = self.scimoz()
        closestPos = None
        endpos = startpos = curpos = sm.currentPos
        #print "Startpos: %d" % (startpos)
        while closestPos is None and startpos > 0:
            #print "\n"
            # Grab up to 1000 bytes/chars of the document text at a time
            # XXX - Byte lengths?? Do we need to get character lengths?? Or is
            #       this value already in characters.
            startpos -= 1000
            startpos = max(0, startpos)
            #print "Getting text %d-%d" % (startpos, endpos)
            text = sm.getTextRange(startpos, endpos)
            #print "Got text of len: %d" % (len(text))
            #print text

            # Go through each regex, get the closest one
            for r in regexlist:
                #print "Regex: %s" % (r)
                matches = re.finditer(r, text)
                # Move to the last match
                match = None
                for match in matches:
                    pass  # This is just to set the right match variable
                if match:
                    # Use the group name for determining the position if provided
                    if getGroupPos:
                        foundPos = startpos + match.start(getGroupPos)
                    else:
                        foundPos = startpos + match.start()
                    #print "foundPos: %d" % (foundPos)
                    #print "matched group: '%s'" % match.group()
                    # We have found a match, check it's the closest to cursor
                    if closestPos is None or foundPos > closestPos:
                        closestPos = foundPos
                        # Highlight the match (for debugging)
                        sm.selectionStart = match.start()
                        sm.selectionEnd = match.end()
        #print "closestPos: %r" % (closestPos)
        return closestPos

    # Utility function for finding text using regex's, searching forwards
    #  regexlist: list - regex's to search for
    #  direction: int - 1 is forwards, 0 is backwards
    #  getGroupPos: string - if set, the match position returned uses the offset
    #                        of this group, instead of the regex start position
    # Returns the position in the document which is the closest match, or None.
    def _find_closest_regex_forwards(self, regexlist, getGroupPos=None):
        sm = self.scimoz()
        closestPos = None
        endpos = startpos = curpos = sm.currentPos
        lastEndPos = sm.length
        while closestPos is None and endpos < lastEndPos:
            # Grab up to 500 bytes/chars of the document text at a time
            # XXX - Byte lengths?? Do we need to get character lengths?? Or is
            #       this value already in characters.
            endpos += 500
            endpos = min(lastEndPos, endpos)
            text = sm.getTextRange(startpos, endpos)

            # Go through each regex, get the closest one
            for r in regexlist:
                match = re.search(r, text)
                if match:
                    # Use the group name for determining the position if provided
                    if getGroupPos:
                        foundPos = startpos + match.start(getGroupPos)
                    else:
                        foundPos = startpos + match.start()
                    # We have found position, check it's the closest to cursor
                    if closestPos is None or foundPos < closestPos:
                        closestPos = foundPos
                        # Highlight the match (for debugging)
                        sm.selectionStart = match.start()
                        sm.selectionEnd = match.end()
        return closestPos

    # From Vi definitions:
    #
    # A sentence is defined as ending at a '.', '!' or '?' followed by either the
    # end of a line, or by a space or tab.  Any number of closing ')', ']', '"'
    # and ''' characters may appear after the '.', '!' or '?' before the spaces,
    # tabs or end of line.  A paragraph and section boundary is also a sentence
    # boundary.
    #
    # A paragraph begins after each empty line. Note that a blank line (only
    # containing white space) is NOT a paragraph boundary. Also note that this
    # does not include a '{' or '}' in the first column.
    #
    # A section begins after a form-feed (<C-L>) in the first column and then
    # matches either a "{" or a "}", or a function/class definition somewhere
    # thereafter.
    # Python: "class", "def"
    # C: "{" and "}"
    #

    # The cursor group sets where we want to be in relation to the regex
    sentenceRegexString = r"""[\.\!\?]['"\)\]]*\s+(?P<cursor>\S)"""
    #paragraphRegexStringBack = r"""[^\r\n]\r?\n\r?\n(?P<cursor>\S)"""
    #paragraphRegexString = r""".\r?\n(?P<cursor>\r?\n)."""
    paragraphRegexString = r"""(\r?\n){2}\s*(?P<cursor>\S)"""
    #sectionRegexString = r"""\r?\n(?P<cursor>\r?\n)"""
    sectionRegexString = r"""(\r?\n){2}[\S\s]*?\r?\n(?P<cursor>(def|class)\s)"""

    regexlistForSentences = [ sentenceRegexString,
                              paragraphRegexString,
                              sectionRegexString ]
    # Used by vim binding (
    def _do_cmd_moveSentenceBegin(self):
        sm = self.scimoz()
        sm.currentPos = max(sm.currentPos, sm.anchor)
        moveToPos = self._find_closest_regex_backwards(self.regexlistForSentences,
                                                       getGroupPos="cursor")
        if moveToPos:
            sm.gotoPos(moveToPos)
        else:
            sm.gotoPos(0)
    # Used by vim binding )
    def _do_cmd_moveSentenceEnd(self):
        sm = self.scimoz()
        sm.currentPos = max(sm.currentPos, sm.anchor)
        moveToPos = self._find_closest_regex_forwards(self.regexlistForSentences,
                                                      getGroupPos="cursor")
        if moveToPos:
            sm.gotoPos(moveToPos)
        else:
            sm.gotoPos(sm.length)

    regexlistForParagraphs = [ paragraphRegexString,
                               sectionRegexString ]
    # Used by vim binding {
    def _do_cmd_moveParagraphBegin(self):
        sm = self.scimoz()
        self._koHistorySvc.note_curr_editor_loc(None)
        sm.currentPos = max(sm.currentPos, sm.anchor)
        moveToPos = self._find_closest_regex_backwards(self.regexlistForParagraphs,
                                                      getGroupPos="cursor")
        if moveToPos:
            sm.gotoPos(moveToPos)
        else:
            sm.gotoPos(0)
    # Used by vim binding }
    def _do_cmd_moveParagraphEnd(self):
        sm = self.scimoz()
        self._koHistorySvc.note_curr_editor_loc(None)
        sm.currentPos = max(sm.currentPos, sm.anchor)
        moveToPos = self._find_closest_regex_forwards(self.regexlistForParagraphs,
                                                      getGroupPos="cursor")
        if moveToPos:
            sm.gotoPos(moveToPos)
        else:
            sm.gotoPos(sm.length)

    regexlistForSection = [ sectionRegexString ]
    # Used by vim binding [[
    def _do_cmd_moveFunctionPrevious(self):
        sm = self.scimoz()
        self._koHistorySvc.note_curr_editor_loc(None)
        sm.currentPos = max(sm.currentPos, sm.anchor)
        moveToPos = self._find_closest_regex_backwards(self.regexlistForSection,
                                                      getGroupPos="cursor")
        if moveToPos:
            sm.gotoPos(moveToPos)
        else:
            sm.gotoPos(0)
    # Used by vim binding ]]
    def _do_cmd_moveFunctionNext(self):
        sm = self.scimoz()
        self._koHistorySvc.note_curr_editor_loc(None)
        sm.currentPos = max(sm.currentPos, sm.anchor)
        moveToPos = self._find_closest_regex_forwards(self.regexlistForSection,
                                                      getGroupPos="cursor")
        if moveToPos:
            sm.gotoPos(moveToPos)
        else:
            sm.gotoPos(sm.length)

    def _do_cmd_linePreviousHome(self):
        sm = self.scimoz()
        sm.lineUp()
        sm.home()
        sm.vCHomeWrap()

    def _do_cmd_lineNextHome(self):
        sm = self.scimoz()
        if sm.lineFromPosition(sm.selectionEnd) + 1 >= sm.lineCount:
            return
        sm.lineDown()
        sm.vCHomeWrap()

    def _do_cmd_home(self):
        sm = self.scimoz()
        # bug 91964 - Allow people to map Home key to always go to column 0.
        if self._koPrefs.getBooleanPref('editHomeKeyFavorsFirstNonSpace'):
            sm.vCHomeWrap()
        else:
            sm.homeWrap()

    def _do_cmd_end(self):
        self.scimoz().lineEndWrap()

    def _is_cmd_transpose_enabled(self):
        return 1 # not really, but we'll deal with edge cases below

    def _do_cmd_transpose(self):
        # transpose two characters to the left
        # Emacs behavior:
        # A: x<|>yz => yx<|>z
        # but
        # B: xy<|><EOL> => yx<|><EOL>
        # and also
        # C: x<EOL><|>yz => xy<EOL><|>z
        # Similarly, D looks a lot like C
        # D: x<EOL>y<|><EOL> => xy<EOL><|><EOL>
        # On an empty line:
        # E: x<EOL-1><|><EOL-2> => <EOL-1>x<|><EOL-2>
        # 
        # Note that #A, #C, and #D both move the object to the right of the cursor
        # over to the left of the object to the left. #B and #E are exceptions.
        #
        scimoz = self.scimoz()
        if scimoz.selectionStart < scimoz.selectionEnd:
            #TODO: transpose all letters in the selections.
            _sendStatusMessage("transpose-characters isn't supported when there's a selection")
            return
        currentPos = scimoz.currentPos
        if currentPos == 0:
            return
        currentLine = scimoz.lineFromPosition(currentPos)
        docLength = scimoz.length
        currentColumn = scimoz.getColumn(currentPos)
        atEndOfLine = scimoz.getLineEndPosition(currentLine) == currentPos
        if currentLine > 0:
            prevEOLPos = scimoz.getLineEndPosition(currentLine - 1)
        else:
            prevEOLPos = -1
        if atEndOfLine:
            nextPos = nextCursorPos = currentPos
            if currentColumn > 1:
                # Case B: transpose prev two chars, don't move forward
                currentPos = scimoz.positionBefore(currentPos)
                prevPos = scimoz.positionBefore(currentPos)
                prevChar = scimoz.getWCharAt(prevPos)
                currChar = scimoz.getWCharAt(currentPos)
            else:
                if prevEOLPos == -1:
                    return
                if currentColumn == 0:
                    if scimoz.getColumn(prevEOLPos) == 0:
                        # This line and prev line are both empty, so do nothing
                        return
                    # Case D: move single char before previous line's EOL to start of this line
                    currChar = scimoz.getTextRange(prevEOLPos, currentPos)
                    prevPos = scimoz.positionBefore(prevEOLPos)
                    prevChar = scimoz.getWCharAt(prevPos)
                else:
                    # Case E: move single char before previous line's EOL
                    prevPos = scimoz.positionBefore(currentPos)
                    currChar = scimoz.getWCharAt(prevPos)
                    prevChar = scimoz.getTextRange(prevEOLPos, prevPos)
                    prevPos = prevEOLPos
        elif currentColumn == 0:
            # Case C: at start of line: transpose prev & current chars,
            #         don't move forward
            # But verify that we aren't at the end of the buffer
            if currentPos >= docLength:
                return
            nextPos = nextCursorPos = scimoz.positionAfter(currentPos)
            prevPos = prevEOLPos
            prevChar = scimoz.getTextRange(prevPos, currentPos)
            currChar = scimoz.getWCharAt(currentPos)
        else:
            # Case A: transpose prev char & current char, and move forward
            nextPos = nextCursorPos = scimoz.positionAfter(currentPos)
            prevPos = scimoz.positionBefore(currentPos)
            prevChar = scimoz.getWCharAt(prevPos)
            currChar = scimoz.getWCharAt(currentPos)
        scimoz.targetStart = prevPos
        scimoz.targetEnd = nextPos
        scimoz.beginUndoAction()
        try:
            scimoz.replaceTarget(currChar + prevChar)
            if nextCursorPos > docLength:
                nextCursorPos = docLength
            scimoz.setSel(nextCursorPos, nextCursorPos)
        finally:
            scimoz.endUndoAction()

    def _get_prev_word_posn(self, scimoz, pos):
        while pos > 0:
            startPos = scimoz.wordStartPosition(pos, True)
            if startPos < pos:
                endPos = scimoz.wordEndPosition(pos, True)
                if startPos < endPos:
                    return [startPos, endPos]
            pos = scimoz.positionBefore(pos)
        return [-1, -1]

    def _get_next_word_posn(self, scimoz, pos):
        lim = scimoz.length
        while pos < lim:
            endPos = scimoz.wordEndPosition(pos, True)
            if endPos > pos:
                startPos = scimoz.wordStartPosition(pos, True)
                if startPos < endPos:
                    return [startPos, endPos]
            pos = scimoz.positionAfter(pos)
        return [-1, -1]

    def _do_cmd_transposeWords(self):
        # emacs behavior:
        # If there's at most one word in the buffer, nothing to do.
        # If we're at the start of the word, swap the current word and the
        # previous word ("swap back").  If it's the first word, swap forward.
        # If we're between two words, swap back.
        # Otherwise, swap the current word and the next word (swap forward).
        scimoz = self.scimoz()
        if scimoz.selectionStart < scimoz.selectionEnd:
            _sendStatusMessage("cmd_transpose is undefined when there's a selection")
            return
        currentPos = scimoz.currentPos

        currentWordStartPos = scimoz.wordStartPosition(currentPos, True)
        currentWordEndPos = scimoz.wordEndPosition(currentPos, True)
        currentWordStartPrevPos = scimoz.positionBefore(currentWordStartPos)
        switchBack = None
        if currentWordStartPos == currentWordEndPos:
            # We're not on a word, so see if we're between two words.
            # Swap back
            currentWordStartPos, currentWordEndPos = \
                    self._get_prev_word_posn(scimoz, currentWordStartPrevPos)
            if currentWordStartPos == -1:
                _sendStatusMessage("No previous word to transpose")
                return
            otherWordStartPos, otherWordEndPos = \
                    self._get_next_word_posn(scimoz, scimoz.positionAfter(currentWordEndPos))
            if otherWordStartPos == -1:
                _sendStatusMessage("No following word to transpose")
                return
            switchBack = False
        elif currentWordStartPos == currentPos:
            # At the start of a word. Is there a prev word to switch back?
            otherWordStartPos, otherWordEndPos = \
                    self._get_prev_word_posn(scimoz, currentWordStartPrevPos)
            if otherWordStartPos > -1:
                switchBack = True
        if switchBack is None:
            otherWordStartPos, otherWordEndPos = \
                    self._get_next_word_posn(scimoz, scimoz.positionAfter(currentWordEndPos))
            if otherWordStartPos == -1:
                _sendStatusMessage("No following word to transpose")
                return
            switchBack = False

        if switchBack:
            word1Extent = [otherWordStartPos, otherWordEndPos]
            word2Extent = [currentWordStartPos, currentWordEndPos]
        else:
            word1Extent = [currentWordStartPos, currentWordEndPos]
            word2Extent = [otherWordStartPos, otherWordEndPos]

        word1 = scimoz.getTextRange(*word1Extent)
        word2 = scimoz.getTextRange(*word2Extent)

        scimoz.beginUndoAction()
        try:
            scimoz.targetStart = word2Extent[0]
            scimoz.targetEnd = word2Extent[1]
            scimoz.replaceTarget(word1)
            scimoz.targetStart = word1Extent[0]
            scimoz.targetEnd = word1Extent[1]
            scimoz.replaceTarget(word2)
            # Move to the end of the right-hand word, just like emacs does.
            scimoz.setSel(word2Extent[1], word2Extent[1])
        finally:
            scimoz.endUndoAction()
            
    def _do_cmd_killLine(self):
        # emacs-style 'kill': if there is nothing but whitespace on the line,
        # that's the same as a cut. If there is something other than whitespace,
        # then it's equivalent to "cut the current line _not including_ the EOL.
        sm = self.scimoz()
        lineNo = sm.lineFromPosition(sm.currentPos)
        endLine = sm.getLineEndPosition(lineNo)
        line = sm.getTextRange(sm.currentPos, endLine)
        if line.strip():
            end = endLine
        else:
            end = min(sm.positionFromLine(lineNo+1), sm.textLength)
        self._doSmartCut(sm.currentPos, end)

    def _do_cmd_removeTrailingWhitespace(self):
        # Two cases -- either there's a selection in which case we
        # want to do the operation only on the selection, or there
        # isn't, in which case we want to do it on the whole document
        sm = self.scimoz()
        selection = sm.selectionStart != sm.selectionEnd
        if not selection:
            start_line = 0
            end_line = sm.lineCount - 1
        else:
            start_line = sm.lineFromPosition(sm.selectionStart)
            start_col = sm.getColumn(sm.selectionStart)
            end_line = sm.lineFromPosition(sm.selectionEnd)
            end_col = sm.getColumn(sm.selectionEnd)
        sm.beginUndoAction()
        for i in xrange(start_line, end_line + 1):
            sm.targetStart = sm.positionFromLine(i)
            sm.targetEnd = sm.getLineEndPosition(i)
            line = sm.getTargetText()[1].rstrip()
            sm.replaceTarget(len(line), line)
        sm.endUndoAction()
        if selection:
            sm.anchor = sm.findColumn(start_line, start_col)
            sm.currentPos = sm.findColumn(end_line, end_col)


charClass = {}
for x in string.letters + string.digits + '_':
    charClass[x] = 'alpha'
WHITESPACE = '\t\n\x0b\x0c\r '  # don't use string.whitespace (bug 81316)
for x in WHITESPACE:
    charClass[x] = 'whitespace'



#---- internal support stuff

def _sendStatusMessage(msg, highlight=False, timeout=3000):
    observerSvc = components.classes["@mozilla.org/observer-service;1"]\
                  .getService(components.interfaces.nsIObserverService)
    sm = components.classes["@activestate.com/koStatusMessage;1"]\
         .createInstance(components.interfaces.koIStatusMessage)
    sm.category = "editor"
    sm.msg = msg
    sm.timeout = timeout
    sm.highlight = highlight
    try:
        observerSvc.notifyObservers(sm, "status_message", None)
    except COMException, ex:
        pass

import sciutils
class ScintillaControllerTestCase(sciutils.SciMozTestCase):
    controller = None
    
    def assertCmdResultIs(self, buffer, cmdName, expected, language="Python"):
        self._setupSciMoz(buffer, language)
        cmd = getattr(self.controller, "_do_cmd_%s" % cmdName)
        cmd()
        eText, eCurrentPos = self._parseBuffer(expected)
        text, currentPos = self.scimoz.text, self.scimoz.currentPos
        got = text[:currentPos] + "<|>" + text[currentPos:]
        comparison = """
--- Expected: -----------------------------------------
%s
--- Got: ----------------------------------------------
%s
-------------------------------------------------------
""" % (expected, got)
        self.assertEqual(text, eText,
                         ("unexpected text after cmd '%s':"%cmdName)
                         + comparison)
        self.assertEqual(currentPos, eCurrentPos,
                         ("unexpected currentPos after cmd '%s':"%cmdName)
                         + comparison)

    def test_cmd_join(self):
        self.assertCmdResultIs("foo\n<|>bar\nspam", "join",
                               "foo\nbar<|> spam")


