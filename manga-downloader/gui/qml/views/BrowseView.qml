import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: browseView
    objectName: "browseView"

    property var mangaCard: null
    property var chapterList: null
    property var downloadControls: null
    property bool detailMode: false
    property string detailError: ""
    property string currentUrl: ""

    DownloadMetadataDialog {
        id: metadataDialog
        onConfirmed: (manga, chapters, format, scanlator) => DownloadBridge.startDownload(manga, chapters, format, scanlator)
    }

    function fetchUrl(url) {
        if (typeof HistoryBridge !== "undefined")
            HistoryBridge.remember(url, SettingsBridge.options.platform, "")
        currentUrl = url
        detailMode = true
        detailError = ""
        mangaCardComp.manga = null
        chapterListComp.setChapters([])
        MangaBridge.fetchManga(url)
    }

    function search(query) {
        query = (query || "").trim()
        if (!query)
            return
        if (typeof HistoryBridge !== "undefined")
            HistoryBridge.remember(query, SettingsBridge.options.platform, "")
        detailMode = false
        detailError = ""
        discoveryView.query = query
        discoveryView.page = 1
        discoveryView.results = []
        DiscoveryBridge.search(query, 1)
    }

    function openManga(manga) {
        if (!manga || !manga.canonical_url)
            return
        fetchUrl(manga.canonical_url)
    }

    function showManga(info) {
        if (typeof HistoryBridge !== "undefined" && currentUrl)
            HistoryBridge.remember(currentUrl, info.source || "comix", info.title || "")
        detailMode = true
        detailError = ""
        mangaCardComp.manga = info
    }

    function showChapters(chapters) {
        chapterListComp.setChapters(chapters)
    }

    function showMangaError(error) {
        detailMode = true
        detailError = error || "Could not load manga information."
    }

    function returnToDiscovery() {
        detailMode = false
        detailError = ""
    }

    function retryDiscovery() {
        if (discoveryView.searchMode)
            DiscoveryBridge.search(discoveryView.query, discoveryView.page)
        else
            DiscoveryBridge.loadHighlights()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        UrlInput {
            id: inputBar
            Layout.fillWidth: true
            Layout.preferredHeight: 56
            onFetchRequested: (url) => browseView.fetchUrl(url)
            onSearchRequested: (query) => browseView.search(query)
        }

        RowLayout {
            visible: !browseView.detailMode
            Layout.fillWidth: true
            spacing: 12
            enabled: !inputBar.isLoading
            Text { text: "PLATFORM"; color: "#E8A54B"; font.pixelSize: 11; font.bold: true }
            ComboBox {
                id: platformSelector
                objectName: "platformSelector"
                model: ["Comix", "MangaDex"]
                currentIndex: SettingsBridge.options.platform === "mangadex" ? 1 : 0
                palette.button: "#252530"
                palette.buttonText: "#F5F5F0"
                onActivated: {
                    SettingsBridge.setValue("platform", currentIndex === 1 ? "mangadex" : "comix")
                    browseView.detailMode = false
                    discoveryView.query = ""
                    discoveryView.results = []
                    discoveryView.trending = []
                    discoveryView.latest = []
                    DiscoveryBridge.loadHighlights()
                }
            }
            Item { Layout.fillWidth: true }
        }

        StackLayout {
            id: contentStack
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: browseView.detailMode ? 1 : 0

            DiscoveryView {
                id: discoveryView
                Layout.fillWidth: true
                Layout.fillHeight: true
                onOpenManga: (manga) => browseView.openManga(manga)
                onPageRequested: (nextPage) => DiscoveryBridge.search(query, nextPage)
                onClearRequested: {
                    query = ""
                    results = []
                    errorMessage = ""
                    if (trending.length === 0 && latest.length === 0)
                        DiscoveryBridge.loadHighlights()
                }
                onRetryRequested: browseView.retryDiscovery()
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34
                    spacing: 10

                    Rectangle {
                        Layout.preferredWidth: 94
                        Layout.preferredHeight: 32
                        radius: 6
                        color: backMouse.containsMouse ? "#252530" : "transparent"
                        border.width: 1
                        border.color: "#5C5C66"
                        Text {
                            anchors.centerIn: parent
                            text: "← BACK"
                            color: "#F5F5F0"
                            font.pixelSize: 10
                            font.weight: Font.Bold
                            font.letterSpacing: 1
                        }
                        MouseArea {
                            id: backMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: browseView.returnToDiscovery()
                        }
                    }

                    Text {
                        text: "MANGA DETAILS"
                        color: "#8B8B99"
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        font.letterSpacing: 1
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    radius: 7
                    color: "#1C1C24"
                    visible: browseView.detailError.length > 0
                    Text {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        verticalAlignment: Text.AlignVCenter
                        text: browseView.detailError
                        color: "#E57373"
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 16

                    MangaCard {
                        id: mangaCardComp
                        Layout.preferredWidth: 280
                        Layout.fillHeight: true
                        visible: manga !== null
                        manga: null
                    }

                    ChapterList {
                        id: chapterListComp
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        preferredScanlator: downloadControlsComp.scanlatorPreference
                    }
                }

                DownloadControls {
                    id: downloadControlsComp
                    objectName: "downloadControls"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 100
                    visible: mangaCardComp.manga !== null
                    scanlators: chapterListComp.getScanlators()
                    onSettingsClicked: settingsDrawer.isOpen = true
                    onFilterChanged: (filter) => chapterListComp.applyFilter(filter)
                    onDownloadClicked: {
                        var selected = chapterListComp.getSelectedChapters()
                        if (selected.length === 0) {
                            browseView.detailError = "Selecione pelo menos um capítulo."
                            return
                        }
                        var format = SettingsBridge.outputFormat
                        if (["pdf", "epub"].indexOf(format) < 0) format = "pdf"
                        metadataDialog.prepare(mangaCardComp.manga, selected, scanlatorPreference, format)
                    }
                }
            }
        }
    }

    Connections {
        target: SettingsBridge
        function onMangaLanguageChanged() {
            if (browseView.detailMode && mangaCardComp.manga && mangaCardComp.manga.source === "mangadex")
                browseView.fetchUrl(browseView.currentUrl)
        }
    }

    Connections {
        target: DiscoveryBridge
        function onHighlightsLoaded(payload) {
            discoveryView.setHighlights(payload)
        }
        function onResultsLoaded(payload) {
            if (payload && payload.query === discoveryView.query)
                discoveryView.setResults(payload)
        }
        function onLoadingChanged(loading) {
            discoveryView.loading = loading
        }
        function onErrorOccurred(error) {
            discoveryView.showError(error)
        }
    }

    Component.onCompleted: {
        if (typeof DiscoveryBridge !== "undefined" && DiscoveryBridge)
            DiscoveryBridge.loadHighlights()
    }

    function getMangaCard() { return mangaCardComp }
    function getChapterList() { return chapterListComp }
    function getDownloadControls() { return downloadControlsComp }
}
