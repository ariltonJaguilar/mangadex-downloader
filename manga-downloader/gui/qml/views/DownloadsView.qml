import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "downloadsView"
    // Keep delegates alive when progress changes. Binding ListView directly to
    // a QVariant list resets the entire view on every jobsChanged signal.
    ListModel { id: jobRows; dynamicRoles: true }
    function syncRows(target, rows) {
        var ids = {}
        for (var i = 0; i < rows.length; i++) ids[rows[i].id] = true
        for (var old = target.count - 1; old >= 0; old--) {
            if (!ids[target.get(old).identifier]) target.remove(old)
        }
        for (var index = 0; index < rows.length; index++) {
            var identifier = rows[index].id
            var found = index
            while (found < target.count && target.get(found).identifier !== identifier) found++
            if (found === target.count) {
                target.insert(index, { identifier: identifier, payload: rows[index] })
            } else {
                if (found !== index) target.move(found, index, 1)
                target.setProperty(index, "payload", rows[index])
            }
        }
    }
    Component.onCompleted: syncRows(jobRows, DownloadBridge.jobs)
    Connections {
        target: DownloadBridge
        function onJobsChanged() { root.syncRows(jobRows, DownloadBridge.jobs) }
    }
    signal openManga(string url)
    function mangaUrl(manga) {
        var source = manga.source || "comix"
        var identifier = manga.hash_id || manga.slug || manga.manga_id
        if (!identifier || ["comix", "mangadex"].indexOf(source) < 0)
            return ""
        return "https://" + (source === "mangadex" ? "mangadex.org" : "comix.to")
                + "/title/" + encodeURIComponent(identifier)
    }
    property string expandedJobId: ""
    function toggleJob(identifier) { expandedJobId = expandedJobId === identifier ? "" : identifier }
    function statusText(status) {
        var labels = { queued: "Na fila", running: "Baixando", cancelling: "Cancelando...",
            complete: "Concluído", partial: "PDF gerado com falhas", failed: "Falhou", cancelled: "Cancelado", paused: "Interrompido" }
        return labels[status] || status
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                Text { text: "DOWNLOADS"; color: "#F5F5F0"; font.pixelSize: 24; font.bold: true }
                Text { text: "Fila e histórico por mangá"; color: "#8B8B99"; font.pixelSize: 13 }
            }
            Button { text: "Limpar finalizados"; onClicked: DownloadBridge.clearFinished() }
        }
        ListView {
            id: jobList
            objectName: "downloadJobList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 12
            model: jobRows
            ScrollBar.vertical: ScrollBar {}
            Text {
                anchors.centerIn: parent
                visible: jobList.count === 0
                text: "Selecione capítulos de um mangá para adicioná-lo à fila."
                color: "#8B8B99"
            }
            delegate: Rectangle {
                id: card
                required property var payload
                property var modelData: payload
                ListModel { id: chapterRows; dynamicRoles: true }
                function refreshChapters() {
                    if (expanded) root.syncRows(chapterRows, modelData.details)
                    else chapterRows.clear()
                }
                onModelDataChanged: if (chapterRows) refreshChapters()
                onExpandedChanged: if (chapterRows) refreshChapters()
                Component.onCompleted: refreshChapters()
                objectName: "downloadCard_" + modelData.id
                property bool expanded: root.expandedJobId === modelData.id
                width: jobList.width
                height: cardContent.implicitHeight + 28
                radius: 12
                color: "#1C1C24"
                border.color: expanded ? "#E8A54B" : "#252530"
                ColumnLayout {
                    id: cardContent
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 14
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                text: card.modelData.manga.title || "Mangá"
                                color: "#F5F5F0"; font.pixelSize: 18; font.bold: true
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.statusText(card.modelData.status) + " \u00b7 " + card.modelData.successful + "/" + card.modelData.total
                                      + " capítulos concluídos \u00b7 " + Math.max(0, card.modelData.total - card.modelData.completed)
                                      + " restantes" + (card.modelData.failed ? " \u00b7 " + card.modelData.failed + " falhas" : "")
                                color: "#E8A54B"; font.pixelSize: 12; wrapMode: Text.Wrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: (card.modelData.manga.source || "comix") + " \u00b7 " + card.modelData.config.output_format.toUpperCase()
                                      + " \u00b7 " + card.modelData.config.download_path
                                color: "#8B8B99"; font.pixelSize: 11; elide: Text.ElideMiddle
                            }
                            Text {
                                text: new Date(card.modelData.created_at).toLocaleString(Qt.locale(), "dd/MM/yyyy hh:mm")
                                color: "#8B8B99"; font.pixelSize: 10
                            }
                            Text {
                                objectName: "downloadLanguageCounts_" + card.modelData.id
                                Layout.fillWidth: true
                                visible: card.modelData.manga.source === "mangadex"
                                text: card.modelData.selected_language
                                      ? "Baixados em " + card.modelData.selected_language.toUpperCase() + ": "
                                        + card.modelData.selected_downloaded + " · Fallback (EN): " + card.modelData.fallback_downloaded
                                      : "Idioma selecionado não registrado neste download antigo."
                                color: "#8B8B99"; font.pixelSize: 12; wrapMode: Text.Wrap
                            }
                        }
                        ColumnLayout {
                            Button {
                                objectName: "openManga_" + card.modelData.id
                                text: "Abrir mangá"
                                enabled: root.mangaUrl(card.modelData.manga).length > 0
                                onClicked: root.openManga(root.mangaUrl(card.modelData.manga))
                            }
                            Button { text: card.expanded ? "Recolher" : "Capítulos"; onClicked: root.toggleJob(card.modelData.id) }
                        }
                    }
                    ProgressBar { Layout.fillWidth: true; from: 0; to: Math.max(1, card.modelData.total); value: card.modelData.completed }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: card.modelData.message || ""
                            visible: text.length > 0
                            color: card.modelData.status === "failed" ? "#E57373" : "#8B8B99"; wrapMode: Text.Wrap
                        }
                        Button {
                            text: "Cancelar"
                            visible: ["queued", "running", "cancelling"].indexOf(card.modelData.status) >= 0
                            enabled: card.modelData.status !== "cancelling"
                            onClicked: DownloadBridge.cancelJob(card.modelData.id)
                        }
                        Button {
                            text: card.modelData.failed > 0 || card.modelData.status === "partial" ? "Tentar falhas e pendentes" : "Retomar pendentes"
                            visible: ["failed", "paused", "cancelled", "partial"].indexOf(card.modelData.status) >= 0
                            onClicked: DownloadBridge.resumeJob(card.modelData.id)
                        }
                    }
                    Column {
                        id: chapterColumn
                        Layout.fillWidth: true
                        visible: card.expanded
                        spacing: 6
                        Repeater {
                            model: chapterRows
                            delegate: Rectangle {
                                required property var payload
                                property var modelData: payload
                                objectName: "downloadChapter_" + card.modelData.id + "_" + modelData.id
                                width: chapterColumn.width
                                height: chapterContent.implicitHeight + 16
                                color: "#252530"; radius: 6
                                ColumnLayout {
                                    id: chapterContent
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    anchors.margins: 8
                                    Text { Layout.fillWidth: true; text: modelData.name; color: "#F5F5F0"; elide: Text.ElideRight }
                                    Text {
                                        Layout.fillWidth: true
                                        text: root.statusText(modelData.status) + " \u00b7 "
                                              + (modelData.total > 0 ? modelData.current + "/" + modelData.total + " páginas baixadas"
                                                 : "Total de páginas não identificado")
                                              + (modelData.language ? " · " + modelData.language.toUpperCase() : "")
                                              + (modelData.is_fallback ? " (fallback)" : "")
                                        color: modelData.status === "failed" ? "#E57373" : "#8B8B99"
                                    }
                                    ProgressBar { Layout.fillWidth: true; from: 0; to: Math.max(1, modelData.total); value: modelData.current }
                                    Text { Layout.fillWidth: true; text: modelData.message; visible: text.length > 0; color: "#8B8B99"; wrapMode: Text.Wrap; font.pixelSize: 11 }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
