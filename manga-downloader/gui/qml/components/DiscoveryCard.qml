import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    property var manga: null
    signal opened(var manga)

    readonly property color bgCard: "#1C1C24"
    readonly property color bgElevated: "#252530"
    readonly property color accentPrimary: "#E8A54B"
    readonly property color textPrimary: "#F5F5F0"
    readonly property color textSecondary: "#8B8B99"
    readonly property color textTertiary: "#5C5C66"

    color: mouseArea.containsMouse ? bgElevated : bgCard
    radius: 10
    border.width: mouseArea.containsMouse ? 1 : 0
    border.color: accentPrimary

    Behavior on color { ColorAnimation { duration: 140 } }
    Behavior on border.width { NumberAnimation { duration: 140 } }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 7

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: width * 1.28
            color: bgElevated
            radius: 7
            clip: true

            Image {
                id: cover
                anchors.fill: parent
                source: root.manga && root.manga.poster_source
                        ? root.manga.poster_source
                        : (root.manga ? (root.manga.poster_url || "") : "")
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                opacity: status === Image.Ready ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 220 } }
            }

            Text {
                anchors.centerIn: parent
                text: "📖"
                font.pixelSize: 30
                opacity: cover.status === Image.Ready ? 0 : 0.3
            }

            Rectangle {
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.margins: 6
                radius: 4
                color: root.manga && root.manga.content_rating === "pornographic"
                       ? "#B84B55"
                       : (root.manga && root.manga.content_rating === "erotica" ? "#C8793C" : "#A87837")
                visible: root.manga && root.manga.content_rating !== "safe"
                width: ratingText.implicitWidth + 10
                height: 20

                Text {
                    id: ratingText
                    anchors.centerIn: parent
                    text: root.manga && root.manga.content_rating === "pornographic" ? "EXPLICIT 18+"
                          : (root.manga && root.manga.content_rating === "erotica" ? "18+" : "SUGGESTIVE")
                    color: "#0A0A0C"
                    font.pixelSize: 9
                    font.weight: Font.Bold
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: root.manga ? root.manga.title : ""
            color: textPrimary
            font.family: "Segoe UI"
            font.pixelSize: 13
            font.weight: Font.DemiBold
            maximumLineCount: 2
            wrapMode: Text.Wrap
            elide: Text.ElideRight
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 5

            Text {
                Layout.fillWidth: true
                text: root.manga ? ((root.manga.manga_type || "Unknown").toUpperCase() +
                      (root.manga.year ? " • " + root.manga.year : "")) : ""
                color: textTertiary
                font.pixelSize: 10
                elide: Text.ElideRight
            }

            Text {
                text: root.manga && root.manga.rated_avg ? "★ " + Number(root.manga.rated_avg).toFixed(1) : ""
                color: accentPrimary
                font.pixelSize: 10
                visible: text.length > 0
            }
        }

        Text {
            Layout.fillWidth: true
            text: root.manga && root.manga.latest_chapter ? "Latest · Ch. " + root.manga.latest_chapter : ""
            color: textSecondary
            font.pixelSize: 10
            elide: Text.ElideRight
            visible: text.length > 0
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.opened(root.manga)
    }
}
