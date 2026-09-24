import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"
import "views"

ApplicationWindow {
    id: root
    
    readonly property color bgDeep: "#0A0A0C"
    readonly property color bgSurface: "#141419"
    
    width: 1100
    height: 750
    minimumWidth: 900
    minimumHeight: 650
    visible: true
    title: "Manga Downloader"
    color: bgDeep
    
    flags: Qt.FramelessWindowHint | Qt.Window
    
    property point dragStart: Qt.point(0, 0)
    onClosing: (close) => {
        if (DownloadBridge.busy) {
            close.accepted = false
            DownloadBridge.prepareShutdown()
        }
    }

    Dialog {
        id: downloadErrorDialog
        anchors.centerIn: parent
        title: "Download error"
        modal: true
        standardButtons: Dialog.Ok
        property string message: ""
        Label { text: downloadErrorDialog.message; wrapMode: Text.Wrap; width: 400 }
    }
    
    RowLayout {
        anchors.fill: parent
        spacing: 0
        
        // SIDEBAR NAVIGATION
        SideBar {
            id: sideBar
            Layout.fillHeight: true
            Layout.preferredWidth: 240
            
            onBrowseClicked: viewStack.currentIndex = 0
            onDownloadsClicked: viewStack.currentIndex = 1
            onSettingsClicked: settingsDrawer.isOpen = true
        }
        
        // MAIN CONTENT AREA
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            
            // TITLE BAR (Modified for the new layout)
            TitleBar {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                
                onMinimizeClicked: root.showMinimized()
                onMaximizeClicked: root.visibility === Window.Maximized ? root.showNormal() : root.showMaximized()
                onCloseClicked: root.close()
                onDragStarted: (pos) => root.dragStart = pos
                onDragMoved: (pos) => { root.x += pos.x - root.dragStart.x; root.y += pos.y - root.dragStart.y }
            }
            
            // VIEW STACK
            StackLayout {
                id: viewStack
                objectName: "viewStack"
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: 0
                
                BrowseView {
                    id: browseView
                }
                
                DownloadsView {
                    id: downloadsView
                    onOpenManga: (url) => {
                        sideBar.activeTab = 0
                        viewStack.currentIndex = 0
                        browseView.fetchUrl(url)
                    }
                }
            }
        }
    }
    
    // SETTINGS DRAWER (Global overlay)
    SettingsDrawer {
        id: settingsDrawer
        anchors.fill: parent
        isOpen: false
    }
    
    // CONNECTIONS TO PYTHON BRIDGES
    Connections {
        target: SettingsBridge
        function onErrorOccurred(message) {
            settingsErrorDialog.message = message
            settingsErrorDialog.open()
        }
    }

    Dialog {
        id: settingsErrorDialog
        anchors.centerIn: parent
        title: "Settings"
        modal: true
        standardButtons: Dialog.Ok
        property string message: ""
        Label { text: settingsErrorDialog.message; wrapMode: Text.Wrap; width: 360 }
    }

    Connections {
        target: MangaBridge
        function onMangaLoaded(info) { browseView.showManga(info) }
        function onChaptersLoaded(chapters) { browseView.showChapters(chapters) }
        function onErrorOccurred(error) { browseView.showMangaError(error) }
    }
    
    Connections {
        target: DownloadBridge
        function onShutdownReady() { root.close() }
        function onErrorOccurred(message) {
            downloadErrorDialog.message = message
            downloadErrorDialog.open()
        }
        function onDownloadStarted() { 
            // Auto switch to downloads tab
            sideBar.activeTab = 1
            viewStack.currentIndex = 1
        }
    }
    
    // STARTUP ANIMATION
    Component.onCompleted: { opacity = 0; startupAnimation.start() }
    
    PropertyAnimation {
        id: startupAnimation
        target: root
        property: "opacity"
        from: 0; to: 1
        duration: 300
        easing.type: Easing.OutCubic
    }
}
