import QtQuick
import QtQuick.Layouts

Item {
    id: root

    property var items: []
    property int minimumColumns: 2
    property int columns: Math.max(minimumColumns, Math.floor(width / 172))
    property int cellHeight: 350
    readonly property int rowCount: items && items.length ? Math.ceil(items.length / columns) : 0
    readonly property int contentHeight: rowCount * cellHeight
    signal openManga(var manga)

    implicitHeight: contentHeight
    height: contentHeight

    GridView {
        anchors.fill: parent
        cellWidth: root.width / root.columns
        cellHeight: root.cellHeight
        model: root.items || []
        interactive: false
        clip: false

        delegate: DiscoveryCard {
            width: Math.max(0, root.width / root.columns - 10)
            height: root.cellHeight - 10
            manga: modelData
            onOpened: root.openManga(manga)
        }
    }
}
