import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: root

    property var trending: []
    property var latest: []
    property var results: []
    property string query: ""
    property int page: 1
    property int lastPage: 1
    property int total: 0
    property bool loading: false
    property string errorMessage: ""
    readonly property bool searchMode: query.length > 0

    signal openManga(var manga)
    signal pageRequested(int page)
    signal clearRequested()
    signal retryRequested()

    function setHighlights(payload) {
        trending = payload && payload.trending ? payload.trending : []
        latest = payload && payload.latest ? payload.latest : []
        errorMessage = ""
    }

    function setResults(payload) {
        results = payload && payload.items ? payload.items : []
        page = payload && payload.page ? payload.page : 1
        lastPage = payload && payload.last_page ? payload.last_page : 1
        total = payload && payload.total ? payload.total : results.length
        errorMessage = ""
    }

    function showError(message) {
        errorMessage = message || "Could not load manga discovery."
    }

    Rectangle {
        anchors.fill: parent
        color: "#0A0A0C"

        ScrollView {
            id: scroll
            anchors.fill: parent
            anchors.margins: 24
            clip: true
            contentWidth: availableWidth
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            ColumnLayout {
                width: scroll.availableWidth
                spacing: 18

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: root.searchMode ? "SEARCH RESULTS" : "DISCOVER MANGA"
                            color: "#F5F5F0"
                            font.family: "Segoe UI"
                            font.pixelSize: 22
                            font.weight: Font.Bold
                            font.letterSpacing: 1
                        }
                        Text {
                            text: root.searchMode
                                  ? (root.total + " titles matching \"" + root.query + "\"")
                                  : "Find something new to download"
                            color: "#8B8B99"
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }
                    }

                    Rectangle {
                        Layout.preferredWidth: 92
                        Layout.preferredHeight: 34
                        radius: 6
                        color: clearMouse.containsMouse ? "#252530" : "transparent"
                        border.width: 1
                        border.color: "#5C5C66"
                        visible: root.searchMode

                        Text {
                            anchors.centerIn: parent
                            text: "CLEAR"
                            color: "#F5F5F0"
                            font.pixelSize: 10
                            font.weight: Font.Bold
                            font.letterSpacing: 1
                        }
                        MouseArea {
                            id: clearMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.clearRequested()
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(52, errorText.implicitHeight + 20)
                    radius: 7
                    color: "#1C1C24"
                    visible: root.errorMessage.length > 0

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 8
                        spacing: 10
                        Text {
                            id: errorText
                            Layout.fillWidth: true
                            text: root.errorMessage
                            color: "#E57373"
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        Rectangle {
                            Layout.preferredWidth: 70
                            Layout.preferredHeight: 28
                            radius: 5
                            color: retryMouse.containsMouse ? "#E8A54B" : "#252530"
                            Text { anchors.centerIn: parent; text: "RETRY"; color: "#0A0A0C"; font.pixelSize: 9; font.weight: Font.Bold }
                            MouseArea {
                                id: retryMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.retryRequested()
                            }
                        }
                    }
                }

                BusyIndicator {
                    Layout.alignment: Qt.AlignHCenter
                    running: root.loading
                    visible: root.loading
                    palette.dark: "#E8A54B"
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    visible: !root.searchMode

                    Text {
                        text: "POPULAR TITLES"
                        color: "#E8A54B"
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        font.letterSpacing: 1
                    }
                    DiscoveryGrid {
                        Layout.fillWidth: true
                        items: root.trending
                        onOpenManga: (manga) => root.openManga(manga)
                    }

                    Text {
                        text: "LATEST UPDATES"
                        color: "#E8A54B"
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        font.letterSpacing: 1
                        Layout.topMargin: 8
                    }
                    DiscoveryGrid {
                        Layout.fillWidth: true
                        items: root.latest
                        onOpenManga: (manga) => root.openManga(manga)
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    visible: root.searchMode && !root.loading && root.results.length > 0

                    DiscoveryGrid {
                        Layout.fillWidth: true
                        items: root.results
                        onOpenManga: (manga) => root.openManga(manga)
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        spacing: 8

                        Rectangle {
                            Layout.preferredWidth: 94
                            Layout.preferredHeight: 34
                            radius: 6
                            color: previousMouse.containsMouse && root.page > 1 ? "#252530" : "transparent"
                            border.width: 1
                            border.color: root.page > 1 ? "#5C5C66" : "#303039"
                            opacity: root.page > 1 ? 1 : 0.45
                            Text { anchors.centerIn: parent; text: "← PREVIOUS"; color: "#F5F5F0"; font.pixelSize: 9; font.weight: Font.Bold }
                            MouseArea {
                                id: previousMouse
                                anchors.fill: parent
                                enabled: root.page > 1
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.pageRequested(root.page - 1)
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            text: "Page " + root.page + " of " + root.lastPage
                            color: "#8B8B99"
                            font.pixelSize: 11
                        }

                        Rectangle {
                            Layout.preferredWidth: 94
                            Layout.preferredHeight: 34
                            radius: 6
                            color: nextMouse.containsMouse && root.page < root.lastPage ? "#E8A54B" : "transparent"
                            border.width: 1
                            border.color: root.page < root.lastPage ? "#E8A54B" : "#303039"
                            opacity: root.page < root.lastPage ? 1 : 0.45
                            Text { anchors.centerIn: parent; text: "NEXT →"; color: root.page < root.lastPage ? "#0A0A0C" : "#F5F5F0"; font.pixelSize: 9; font.weight: Font.Bold }
                            MouseArea {
                                id: nextMouse
                                anchors.fill: parent
                                enabled: root.page < root.lastPage
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.pageRequested(root.page + 1)
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 110
                    visible: root.searchMode && !root.loading && root.results.length === 0 && root.errorMessage.length === 0
                    spacing: 8
                    Text { Layout.alignment: Qt.AlignHCenter; text: "⌕"; color: "#5C5C66"; font.pixelSize: 32 }
                    Text { Layout.alignment: Qt.AlignHCenter; text: "No manga found"; color: "#F5F5F0"; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Text { Layout.alignment: Qt.AlignHCenter; text: "Try a different title or keyword."; color: "#8B8B99"; font.pixelSize: 11 }
                }

                Item { Layout.preferredHeight: 12 }
            }
        }
    }
}

