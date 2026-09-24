import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "settingsDrawer"
    
    property bool isOpen: false
    property bool mangaLoading: false

    Connections {
        target: MangaBridge
        function onLoadingChanged(loading) { root.mangaLoading = loading }
    }
    
    // Theme colors
    readonly property color bgDeep: "#0A0A0C"
    readonly property color bgSurface: "#141419"
    readonly property color bgCard: "#1C1C24"
    readonly property color bgElevated: "#252530"
    readonly property color accentPrimary: "#E8A54B"
    readonly property color textPrimary: "#F5F5F0"
    readonly property color textSecondary: "#8B8B99"
    readonly property color textTertiary: "#5C5C66"
    readonly property color error: "#E57373"
    readonly property color success: "#7CB342"
    
    visible: isOpen
    color: Qt.rgba(0, 0, 0, 0.7)
    
    // Click outside to close
    MouseArea {
        anchors.fill: parent
        onClicked: root.isOpen = false
    }
    
    // Settings Panel
    Rectangle {
        id: panel
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 380
        color: bgSurface
        
        x: root.isOpen ? 0 : width
        Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        
        MouseArea { anchors.fill: parent } // Prevent click-through
        
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 24
            spacing: 24
            
            // Header
            RowLayout {
                Layout.fillWidth: true
                
                Text {
                    text: "⚙️ SETTINGS"
                    font.family: "Segoe UI"
                    font.pixelSize: 22
                    font.weight: Font.Bold
                    color: textPrimary
                }
                
                Item { Layout.fillWidth: true }
                
                Rectangle {
                    width: 32; height: 32; radius: 6
                    color: closeArea.containsMouse ? bgElevated : "transparent"
                    Text { anchors.centerIn: parent; text: "×"; font.pixelSize: 18; font.weight: Font.Bold; color: textSecondary }
                    MouseArea { id: closeArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.isOpen = false }
                }
            }
            
            ScrollView {
                id: settingsScroll
                objectName: "settingsScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                width: settingsScroll.availableWidth
                spacing: 20
                
                // Output Format
                SettingItem {
                    label: "Output Format"
                    RowLayout {
                        spacing: 8
                        Repeater {
                            model: ["images", "pdf", "epub", "cbz"]
                            Rectangle {
                                width: 75; height: 36; radius: 8
                                color: (SettingsBridge ? SettingsBridge.outputFormat : "") === modelData ? accentPrimary : bgElevated
                                border.color: (SettingsBridge ? SettingsBridge.outputFormat : "") === modelData ? accentPrimary : textTertiary
                                border.width: 1
                                
                                Text { 
                                    anchors.centerIn: parent
                                    text: modelData.toUpperCase()
                                    font.pixelSize: 12
                                    font.weight: Font.Bold
                                    color: (SettingsBridge ? SettingsBridge.outputFormat : "") === modelData ? bgDeep : textSecondary 
                                }
                                MouseArea { 
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: SettingsBridge.setValue("output_format", modelData) 
                                }
                            }
                        }
                    }
                }
                
                // Keep Images
                SettingItem {
                    label: "Manter páginas e reutilizar ao retomar"
                    ToggleSwitch {
                        checked: SettingsBridge ? SettingsBridge.keepImages : false
                        onToggled: (value) => SettingsBridge.setValue("keep_images", value)
                    }
                }
                
                // Enable Logs
                SettingItem {
                    label: "Enable Debug Logs"
                    ToggleSwitch {
                        checked: SettingsBridge ? SettingsBridge.options.enable_logs : false
                        onToggled: (value) => SettingsBridge.setValue("enable_logs", value)
                    }
                }
                
                // Run Browser Headless
                SettingItem {
                    label: "Navegador em segundo plano"
                    Column {
                        spacing: 6
                        ToggleSwitch {
                            checked: SettingsBridge ? SettingsBridge.headless : true
                            onToggled: (value) => SettingsBridge.setValue("headless", value)
                        }
                        Text {
                            width: 300; wrapMode: Text.Wrap
                            text: SettingsBridge.headless
                                  ? "Abre uma janela apenas se o site pedir verificação. Resolva o captcha; a janela fecha e o download continua em segundo plano."
                                  : "O navegador fica visível durante todo o trabalho. Ative para mostrar a janela somente quando precisar de verificação."
                            color: textSecondary; font.pixelSize: 11
                        }
                    }
                }
                
                // Download Path
                SettingItem {
                    label: "Download Path"
                    Column {
                    spacing: 8
                    Rectangle {
                        width: 220; height: 40; radius: 8
                        color: bgElevated
                        border.color: textTertiary; border.width: 1
                        
                        TextInput {
                            anchors.fill: parent
                            anchors.leftMargin: 12; anchors.rightMargin: 12
                            verticalAlignment: Text.AlignVCenter
                            text: SettingsBridge ? SettingsBridge.downloadPath : ""
                            color: textPrimary
                            font.pixelSize: 14
                            clip: true
                            selectByMouse: true
                            onEditingFinished: if (SettingsBridge) SettingsBridge.setValue("download_path", text)
                        }
                    }
                    Button {
                        objectName: "chooseDownloadFolderButton"
                        text: "Escolher pasta..."
                        palette.button: bgElevated
                        palette.buttonText: accentPrimary
                        onClicked: SettingsBridge.chooseDownloadFolder()
                    }
                    }
                }

                SettingItem {
                    label: "Arquivos temporários"
                    Column {
                        spacing: 8
                        TextField {
                            objectName: "temporaryPathInput"
                            width: 300
                            text: SettingsBridge.tempPath
                            color: textPrimary
                            background: Rectangle { color: bgElevated; radius: 8; border.color: textTertiary }
                            onEditingFinished: if (text !== SettingsBridge.tempPath) SettingsBridge.setValue("temp_path", text)
                        }
                        Button {
                            objectName: "chooseTemporaryFolderButton"
                            text: "Escolher pasta temporária..."
                            palette.button: bgElevated
                            palette.buttonText: accentPrimary
                            onClicked: SettingsBridge.chooseTemporaryFolder()
                        }
                        Text {
                            width: 300; wrapMode: Text.Wrap
                            text: "Com Manter páginas ativo, as páginas completas ficam salvas para retomar mesmo após interrupções. Arquivos de conversão são temporários. Downloads na fila mantêm a pasta original."
                            color: textSecondary; font.pixelSize: 11
                        }
                        Button {
                            objectName: "clearDownloadedPagesButton"
                            text: "Limpar todas as páginas baixadas"
                            palette.button: bgElevated
                            palette.buttonText: accentPrimary
                            onClicked: SettingsBridge.clearDownloadedPages()
                        }
                        Text {
                            id: clearPagesStatus
                            width: 300; wrapMode: Text.Wrap
                            text: "Remove todas as páginas registradas por esta versão: cache, cópias após conversão e downloads no formato imagens, inclusive em pastas anteriores. PDF/CBZ são preservados."
                            color: textSecondary; font.pixelSize: 11
                        }
                        Connections {
                            target: SettingsBridge
                            function onPagesCleared(message) { clearPagesStatus.text = message }
                        }
                    }
                }
                SettingItem {
                    label: "PDF — largura das páginas"
                    Column {
                        spacing: 6
                        CheckBox {
                            text: "Automática"
                            checked: SettingsBridge.options.pdf_page_width === 0
                            palette.windowText: textPrimary
                            onClicked: SettingsBridge.setValue("pdf_page_width", checked ? 0 : 1200)
                        }
                        SpinBox {
                            objectName: "pdfWidthInput"
                            from: 600; to: 4000; stepSize: 100
                            editable: true
                            enabled: SettingsBridge.options.pdf_page_width !== 0
                            value: SettingsBridge.options.pdf_page_width || 1200
                            palette.button: bgElevated
                            palette.text: textPrimary
                            palette.base: bgElevated
                            onValueModified: SettingsBridge.setValue("pdf_page_width", value)
                        }
                        Text {
                            width: 300; wrapMode: Text.Wrap
                            text: "Automática: maior largura do capítulo. Manual: 600 a 4000 px. Webtoon une as imagens em tiras verticais por capítulo."
                            color: textSecondary; font.pixelSize: 11
                        }
                    }
                }
                SettingItem {
                    label: "MangaDex — idioma dos capítulos"
                    ComboBox {
                        objectName: "languageSelector"
                        width: 300
                        enabled: !root.mangaLoading
                        property var codes: ["pt-br", "pt", "en", "es", "es-la", "fr", "ja"]
                        model: ["Português (BR)", "Português (PT)", "English", "Español", "Español (LATAM)", "Français", "日本語"]
                        currentIndex: Math.max(0, codes.indexOf(SettingsBridge.options.mangadex_language))
                        palette.button: bgElevated
                        palette.buttonText: textPrimary
                        onActivated: SettingsBridge.setValue("mangadex_language", codes[currentIndex])
                    }
                }
                SettingItem {
                    label: "MangaDex — imagens comprimidas"
                    ToggleSwitch {
                        objectName: "compressedImagesToggle"
                        checked: SettingsBridge.options.use_compressed_image
                        onToggled: (value) => SettingsBridge.setValue("use_compressed_image", value)
                    }
                }
                SettingItem {
                    label: "MangaDex — completar com inglês"
                    Column {
                        spacing: 6
                        ToggleSwitch {
                            objectName: "fallbackEnglishToggle"
                            checked: SettingsBridge.options.fallback_english
                            onToggled: (value) => SettingsBridge.setValue("fallback_english", value)
                        }
                        Text {
                            width: 300; wrapMode: Text.Wrap
                            text: "Usa inglês onde faltar tradução no idioma escolhido. Recarregue a obra após alterar esta opção."
                            color: textSecondary; font.pixelSize: 11
                        }
                    }
                }
                
                // Max Chapter Workers
                SettingItem {
                    label: "Max Chapter Workers (1-10)"
                    NumberInput {
                        value: SettingsBridge ? SettingsBridge.maxChapterWorkers : 3
                        minValue: 1; maxValue: 10
                        onValueModified: (value) => SettingsBridge.setValue("max_chapter_workers", value)
                    }
                }
                
                // Max Image Workers
                SettingItem {
                    label: "Max Image Workers (1-20)"
                    NumberInput {
                        value: SettingsBridge ? SettingsBridge.maxImageWorkers : 5
                        minValue: 1; maxValue: 20
                        onValueModified: (value) => SettingsBridge.setValue("max_image_workers", value)
                    }
                }
                
                Item { Layout.fillHeight: true }
                
                // Reset Button
                Rectangle {
                    Layout.alignment: Qt.AlignHCenter
                    width: 180; height: 44; radius: 8
                    color: resetArea.containsMouse ? error : "transparent"
                    border.color: error; border.width: 2
                    
                    Text { 
                        anchors.centerIn: parent
                        text: "Reset to Defaults"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        color: resetArea.containsMouse ? textPrimary : error 
                    }
                    
                    MouseArea {
                        id: resetArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: SettingsBridge.resetToDefaults()
                    }
                }
                }
            }
        }
    }
    
    // ═══════════════════════════════════════════════════════════════
    // SETTING ITEM COMPONENT
    // ═══════════════════════════════════════════════════════════════
    component SettingItem: ColumnLayout {
        property string label: ""
        default property alias content: contentArea.children
        
        Layout.fillWidth: true
        spacing: 8
        
        Text { 
            text: label
            font.pixelSize: 13
            font.weight: Font.Medium
            color: textSecondary 
        }
        Row { id: contentArea; spacing: 8 }
    }
    
    // ═══════════════════════════════════════════════════════════════
    // TOGGLE SWITCH COMPONENT
    // ═══════════════════════════════════════════════════════════════
    component ToggleSwitch: Rectangle {
        property bool checked: false
        signal toggled(bool value)
        
        width: 52; height: 28; radius: 14
        color: checked ? accentPrimary : bgElevated
        border.color: checked ? accentPrimary : textTertiary; border.width: 1
        
        Behavior on color { ColorAnimation { duration: 150 } }
        
        Rectangle {
            width: 22; height: 22; radius: 11
            anchors.verticalCenter: parent.verticalCenter
            x: parent.checked ? parent.width - width - 3 : 3
            color: textPrimary
            
            Behavior on x { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
        }
        
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: parent.toggled(!parent.checked)
        }
    }
    
    // ═══════════════════════════════════════════════════════════════
    // NUMBER INPUT COMPONENT (custom spinbox replacement)
    // ═══════════════════════════════════════════════════════════════
    component NumberInput: Rectangle {
        property int value: 0
        property int minValue: 0
        property int maxValue: 100
        signal valueModified(int value)
        
        width: 120; height: 40; radius: 8
        color: bgElevated
        border.color: textTertiary; border.width: 1
        
        RowLayout {
            anchors.fill: parent
            anchors.margins: 4
            spacing: 0
            
            // Minus button
            Rectangle {
                Layout.preferredWidth: 32; Layout.fillHeight: true
                radius: 6
                color: minusArea.containsMouse ? bgCard : "transparent"
                
                Text {
                    anchors.centerIn: parent
                    text: "−"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: value > minValue ? accentPrimary : textTertiary
                }
                
                MouseArea {
                    id: minusArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (value > minValue) valueModified(value - 1)
                }
            }
            
            // Value display
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                
                Text {
                    anchors.centerIn: parent
                    text: value
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                    color: textPrimary
                }
            }
            
            // Plus button
            Rectangle {
                Layout.preferredWidth: 32; Layout.fillHeight: true
                radius: 6
                color: plusArea.containsMouse ? bgCard : "transparent"
                
                Text {
                    anchors.centerIn: parent
                    text: "+"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: value < maxValue ? accentPrimary : textTertiary
                }
                
                MouseArea {
                    id: plusArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (value < maxValue) valueModified(value + 1)
                }
            }
        }
    }
}
