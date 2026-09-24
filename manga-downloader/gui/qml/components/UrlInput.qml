import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Rectangle {
    id: root
    objectName: "searchInput"
    
    // Theme colors
    readonly property color bgCard: "#1C1C24"
    readonly property color bgElevated: "#252530"
    readonly property color accentPrimary: "#E8A54B"
    readonly property color accentGradientEnd: "#D4873A"
    readonly property color textPrimary: "#F5F5F0"
    readonly property color textTertiary: "#5C5C66"
    readonly property color bgDeep: "#0A0A0C"
    
    color: bgCard
    radius: 12
    
    signal fetchRequested(string url)
    signal searchRequested(string query)

    property bool mangaLoading: false
    property bool discoveryLoading: false
    readonly property bool isLoading: mangaLoading || discoveryLoading
    readonly property var historyItems: typeof HistoryBridge !== "undefined" ? HistoryBridge.items : []

    function openHistory() {
        if (root.historyItems.length > 0)
            historyPopup.open()
    }

    function dismissSearch() {
        historyPopup.close()
        urlField.focus = false
        urlField.deselect()
    }

    // Observe presses without grabbing them from buttons, links or other fields.
    TapHandler {
        id: outsidePress
        parent: root.Window.window ? root.Window.window.contentItem : root
        enabled: urlField.focus || historyPopup.visible
        onPressedChanged: {
            if (!pressed) return
            var inField = urlField.mapFromItem(parent, point.position.x, point.position.y)
            if (urlField.contains(inField)) return
            if (historyPopup.visible) {
                var inHistory = historyPopup.contentItem.mapFromItem(parent, point.position.x, point.position.y)
                if (inHistory.x >= -historyPopup.padding && inHistory.y >= -historyPopup.padding
                        && inHistory.x <= historyPopup.contentItem.width + historyPopup.padding
                        && inHistory.y <= historyPopup.contentItem.height + historyPopup.padding) return
            }
            root.dismissSearch()
        }
    }

    Connections {
        target: root.Window.window
        function onActiveChanged() {
            if (!root.Window.window.active) historyPopup.close()
        }
    }

    Popup {
        id: historyPopup
        objectName: "searchHistoryPopup"
        y: root.height + 4
        width: root.width
        height: Math.min(340, historyList.contentHeight + 16)
        padding: 8
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        onClosed: {
            if (root.Window.window && root.Window.window.active) {
                urlField.focus = false
                urlField.deselect()
            }
        }
        background: Rectangle { color: root.bgCard; radius: 8; border.color: root.textTertiary }
        contentItem: ListView {
            id: historyList
            clip: true
            model: root.historyItems
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                enabled: !root.mangaLoading
                width: historyList.width
                height: 54
                contentItem: Column {
                    Text { width: parent.width; text: modelData.title; color: root.textPrimary; elide: Text.ElideRight }
                    Text { width: parent.width; text: modelData.platform + " · " + (modelData.kind === "title" ? "Mangá" : "Pesquisa"); color: root.textTertiary; font.pixelSize: 11 }
                }
                onClicked: {
                    var entry = modelData
                    historyPopup.close()
                    urlField.text = entry.value
                    if (entry.kind === "search")
                        SettingsBridge.setValue("platform", entry.platform)
                    root.submit()
                }
            }
        }
    }

    function isUrlInput(value) {
        var normalized = (value || "").trim().toLowerCase()
        return normalized.indexOf("http://") === 0
                || normalized.indexOf("https://") === 0
                || normalized.indexOf("comix.to/title/") >= 0
                || normalized.indexOf("mangadex.org/title/") >= 0
    }

    function submit() {
        var value = urlField.text.trim()
        if (!value || root.mangaLoading)
            return
        historyPopup.close()
        if (root.isUrlInput(value))
            root.fetchRequested(value)
        else
            root.searchRequested(value)
    }
    
    RowLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8
        
        // LINK ICON
        Text {
            text: root.isUrlInput(urlField.text) ? "🔗" : "⌕"
            font.pixelSize: 18
            opacity: 0.7
        }
        
        // TEXT INPUT
        TextField {
            id: urlField
            objectName: "inputField"
            Layout.fillWidth: true
            Layout.fillHeight: true
            
            placeholderText: "Search or paste a Comix / MangaDex URL..."
            placeholderTextColor: textTertiary
            color: textPrimary
            font.family: "Segoe UI"
            font.pixelSize: 14
            onActiveFocusChanged: {
                // Window activation (Alt+Tab) is not a request to open history.
                if (activeFocus && (focusReason === Qt.TabFocusReason || focusReason === Qt.BacktabFocusReason))
                    root.openHistory()
            }
            TapHandler {
                onTapped: {
                    urlField.forceActiveFocus(Qt.MouseFocusReason)
                    root.openHistory()
                }
            }
            Keys.onEscapePressed: root.dismissSearch()
            
            background: Rectangle {
                color: "transparent"
                
                // Animated underline
                Rectangle {
                    anchors.bottom: parent.bottom
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: urlField.activeFocus ? parent.width : 0
                    height: 2
                    color: accentPrimary
                    radius: 1
                    
                    Behavior on width {
                        NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
                    }
                }
                
                // Static dim underline
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: 1
                    color: textTertiary
                    opacity: 0.3
                }
            }
            
            Keys.onReturnPressed: {
                root.submit()
            }
        }
        
        // FETCH BUTTON
        Rectangle {
            id: fetchButton
            
            Layout.preferredWidth: 100
            Layout.fillHeight: true
            
            radius: 6
            
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: accentPrimary }
                GradientStop { position: 1.0; color: accentGradientEnd }
            }
            
            scale: fetchMouseArea.pressed ? 0.97 : (fetchMouseArea.containsMouse ? 1.02 : 1.0)
            
            Behavior on scale {
                NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
            }
            
            Text {
                anchors.centerIn: parent
                text: root.isLoading ? "..." : (root.isUrlInput(urlField.text) ? "FETCH" : "SEARCH")
                font.family: "Segoe UI"
                font.pixelSize: 12
                font.weight: Font.Bold
                color: bgDeep
                font.letterSpacing: 1
            }
            
            MouseArea {
                id: fetchMouseArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                enabled: !root.mangaLoading
                
                onClicked: {
                    root.submit()
                }
            }
        }
    }
    
    Connections {
        target: MangaBridge
        function onLoadingChanged(loading) {
            root.mangaLoading = loading
        }
    }

    Connections {
        target: DiscoveryBridge
        function onLoadingChanged(loading) {
            root.discoveryLoading = loading
        }
    }
}
