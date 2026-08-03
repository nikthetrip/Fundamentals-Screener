//
//  FundamentalsScreenerApp.swift — L'ingresso.
//

import SwiftUI

@main
struct FundamentalsScreenerApp: App {
    @StateObject private var store = DataStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .tint(Palette.accent)
                // Tema scuro sempre. I colori restano definiti a coppie in
                // Theme.swift — costa nulla e serve se un giorno si vuole
                // seguire l'impostazione di sistema — ma l'applicazione si
                // presenta con la versione scura: e' quella su cui il verde e
                // il rosso smorzati dei giudizi si distinguono meglio, ed e'
                // quella che si guarda senza abbagliare la sera.
                .preferredColorScheme(.dark)
                .task { await store.start() }
        }
    }
}

/// Decide che cosa mostrare in base allo stato dei dati.
///
/// TRE STATI, NON DUE. "Sto scaricando" e "non ci sono riuscito" non sono la
/// stessa cosa, e nessuno dei due e' "lista vuota": una lista vuota fa pensare
/// che il filtro sia troppo stretto e manda a cercare un problema che non c'e'.
struct RootView: View {
    @EnvironmentObject private var store: DataStore

    var body: some View {
        switch store.state {
        case .ready:
            ScreenerView()
        case .failed(let message):
            FirstRunView(state: .failed(message))
        default:
            FirstRunView(state: store.state)
        }
    }
}

/// La schermata del primo avvio: l'unica volta in cui c'e' davvero da
/// aspettare, perche' il database non e' ancora sul telefono.
struct FirstRunView: View {
    let state: DataStore.State
    @EnvironmentObject private var store: DataStore

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            VStack(spacing: 20) {
                Spacer()
                Text("Fundamentals Screener")
                    .font(.displayLarge)
                    .foregroundStyle(Palette.ink)

                switch state {
                case .failed(let message):
                    VStack(spacing: 14) {
                        Text("Could not download the data.")
                            .font(.subheadline)
                            .foregroundStyle(Palette.ink)
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(Palette.inkFaint)
                            .multilineTextAlignment(.center)
                        Button("Retry") {
                            Task { await store.refresh(force: true) }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    .padding(.horizontal, 32)

                case .unpacking:
                    ProgressView()
                    Text("Preparing the database")
                        .font(.footnote).foregroundStyle(Palette.inkMuted)

                default:
                    ProgressView()
                    VStack(spacing: 4) {
                        Text("Downloading data")
                            .font(.footnote).foregroundStyle(Palette.inkMuted)
                        Text("About 7 MB, once. After that the app works\nwithout a connection.")
                            .font(.caption2)
                            .foregroundStyle(Palette.inkFaint)
                            .multilineTextAlignment(.center)
                    }
                }
                Spacer()
                Text("SEC EDGAR data · processed locally")
                    .font(.caption2)
                    .foregroundStyle(Palette.inkFaint)
                    .padding(.bottom, 24)
            }
        }
    }
}
