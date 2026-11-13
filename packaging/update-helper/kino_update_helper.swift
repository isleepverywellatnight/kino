import Darwin
import Foundation

// Runs outside KINO.app so the application bundle can be replaced after exit.
// The helper is copied to a private user directory before it is launched.

struct Job: Decodable {
    let action: String
    let pid: Int32
    let app_path: String
    let candidate_path: String
    let backup_path: String
    let backup_version: String
    let version: String
    let data_schema: Int
    let status_path: String
    let pending_path: String
    let ack_path: String
    let nonce: String
}

enum UpdateFailure: Error {
    case invalidJob(String)
    case timedOut(String)
    case failed(String)
}

let files = FileManager.default

func writeJSON(_ value: [String: Any], to path: String) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    try data.write(to: URL(fileURLWithPath: path), options: .atomic)
}

func bundleVersion(_ path: String) -> String {
    let plist = URL(fileURLWithPath: path).appendingPathComponent("Contents/Info.plist")
    guard let data = try? Data(contentsOf: plist),
          let info = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
          info["CFBundleIdentifier"] as? String == "com.kino.desktop" else { return "" }
    return info["CFBundleShortVersionString"] as? String ?? ""
}

func validate(_ job: Job) throws {
    let app = URL(fileURLWithPath: job.app_path).standardizedFileURL
    let backup = URL(fileURLWithPath: job.backup_path).standardizedFileURL
    let testRoot = app.path.hasPrefix("/private/tmp/kino-update-test-")
        || app.path.hasPrefix("/tmp/kino-update-test-")
    guard app.path == "/Applications/KINO.app" || testRoot else {
        throw UpdateFailure.invalidJob("Emplacement de KINO non autorisé : \(app.path).")
    }
    guard app.lastPathComponent == "KINO.app" else {
        throw UpdateFailure.invalidJob("Nom de l'application incorrect.")
    }
    guard backup.deletingLastPathComponent().resolvingSymlinksInPath().path
            == app.deletingLastPathComponent().resolvingSymlinksInPath().path,
          backup.lastPathComponent.hasPrefix(".KINO.backup-"),
          backup.pathExtension == "app" else {
        throw UpdateFailure.invalidJob("Emplacement de sauvegarde invalide : \(backup.path).")
    }
    guard job.nonce.range(of: "^[0-9a-f]{24}$", options: .regularExpression) != nil,
          job.data_schema == 1,
          job.action == "install" || job.action == "rollback" else {
        throw UpdateFailure.invalidJob("Version de tâche ou action invalide.")
    }
    let stateDir = URL(fileURLWithPath: job.status_path).deletingLastPathComponent().standardizedFileURL
    guard URL(fileURLWithPath: job.pending_path).deletingLastPathComponent().standardizedFileURL == stateDir,
          URL(fileURLWithPath: job.ack_path).deletingLastPathComponent().standardizedFileURL == stateDir,
          URL(fileURLWithPath: job.ack_path).lastPathComponent == "ack-\(job.nonce).json" else {
        throw UpdateFailure.invalidJob("Fichiers de suivi invalides.")
    }
    if job.action == "install" {
        guard URL(fileURLWithPath: job.candidate_path).lastPathComponent == "KINO.app",
              bundleVersion(job.candidate_path) == job.version,
              bundleVersion(job.app_path) == job.backup_version else {
            throw UpdateFailure.invalidJob("Version de l'application incohérente.")
        }
    } else {
        guard bundleVersion(job.backup_path) == job.version,
              bundleVersion(job.app_path) != "" else {
            throw UpdateFailure.invalidJob("Version de retour arrière absente.")
        }
    }
}

func waitForExit(_ pid: Int32, seconds: TimeInterval = 60) throws {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        if kill(pid, 0) != 0 && errno == ESRCH { return }
        Thread.sleep(forTimeInterval: 0.2)
    }
    throw UpdateFailure.timedOut("KINO ne s'est pas fermé pour la mise à jour.")
}

func swap(_ a: String, _ b: String) throws {
    let result = a.withCString { pa in
        b.withCString { pb in
            renameatx_np(AT_FDCWD, pa, AT_FDCWD, pb, UInt32(RENAME_SWAP))
        }
    }
    if result != 0 {
        throw UpdateFailure.failed("Échange atomique impossible (code \(errno)).")
    }
}

func runSelfCheck(_ app: String) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: app + "/Contents/MacOS/KINO")
    process.arguments = ["--self-check"]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try process.run()
    let deadline = Date().addingTimeInterval(45)
    while process.isRunning && Date() < deadline {
        Thread.sleep(forTimeInterval: 0.2)
    }
    if process.isRunning {
        process.terminate()
        throw UpdateFailure.timedOut("Le nouveau KINO ne démarre pas.")
    }
    guard process.terminationStatus == 0 else {
        throw UpdateFailure.failed("L'auto-vérification de KINO a échoué.")
    }
}

func launch(_ app: String) throws -> Process {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: app + "/Contents/MacOS/KINO")
    process.currentDirectoryURL = URL(fileURLWithPath: app)
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try process.run()
    return process
}

func stop(_ process: Process) {
    if !process.isRunning { return }
    process.terminate()
    let deadline = Date().addingTimeInterval(3)
    while process.isRunning && Date() < deadline {
        Thread.sleep(forTimeInterval: 0.1)
    }
    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    process.waitUntilExit()
}

func waitForReady(_ job: Job, process: Process) throws {
    let deadline = Date().addingTimeInterval(45)
    while Date() < deadline {
        if let data = try? Data(contentsOf: URL(fileURLWithPath: job.ack_path)),
           let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           value["nonce"] as? String == job.nonce,
           value["ready"] as? Bool == true {
            return
        }
        if !process.isRunning { break }
        Thread.sleep(forTimeInterval: 0.25)
    }
    stop(process)
    throw UpdateFailure.timedOut("La fenêtre de KINO n'a pas confirmé son démarrage.")
}

func status(_ job: Job, message: String, backupPath: String, backupVersion: String) throws {
    try writeJSON([
        "message": message,
        "version": bundleVersion(job.app_path),
        "backup_path": backupPath,
        "backup_version": backupVersion,
        "data_schema": job.data_schema,
        "updated_at": Date().timeIntervalSince1970,
    ], to: job.status_path)
}

func run(_ job: Job) throws {
    try validate(job)
    try waitForExit(job.pid)

    let app = job.app_path
    let backup = job.backup_path
    let previousVersion = bundleVersion(app)
    var swapped = false
    var rollbackSource = backup
    var temporary = ""
    var launched: Process? = nil
    do {
        if job.action == "install" {
            temporary = URL(fileURLWithPath: app).deletingLastPathComponent()
                .appendingPathComponent(".KINO.swap-\(job.nonce).app").path
            try files.copyItem(atPath: job.candidate_path, toPath: temporary)
            guard bundleVersion(temporary) == job.version else {
                throw UpdateFailure.failed("Copie préparée incohérente.")
            }
            try swap(app, temporary)
            swapped = true
            rollbackSource = temporary
        } else {
            try swap(app, backup)
            swapped = true
        }

        try runSelfCheck(app)
        try writeJSON([
            "nonce": job.nonce,
            "version": job.version,
            "ack_path": job.ack_path,
        ], to: job.pending_path)
        let process = try launch(app)
        launched = process
        try waitForReady(job, process: process)

        if job.action == "install" {
            try files.moveItem(atPath: temporary, toPath: backup)
            rollbackSource = backup
            try status(job, message: "Mise à jour installée.", backupPath: backup, backupVersion: previousVersion)
        } else {
            try status(job, message: "Version précédente restaurée.", backupPath: backup, backupVersion: previousVersion)
        }
        try? files.removeItem(atPath: job.pending_path)
    } catch {
        try? files.removeItem(atPath: job.pending_path)
        if let process = launched { stop(process) }
        if swapped {
            try? swap(app, rollbackSource)
        }
        if !temporary.isEmpty && files.fileExists(atPath: temporary) {
            try? files.removeItem(atPath: temporary)
        }
        let text = "Mise à jour annulée : \(error.localizedDescription)"
        try? status(job, message: text, backupPath: job.action == "rollback" ? backup : "", backupVersion: job.action == "rollback" ? job.version : "")
        _ = try? launch(app)
        throw error
    }
}

do {
    guard CommandLine.arguments.count == 2 else { throw UpdateFailure.invalidJob("Fichier de tâche manquant.") }
    let jobURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let attrs = try files.attributesOfItem(atPath: jobURL.path)
    guard (attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600 else {
        throw UpdateFailure.invalidJob("Fichier de tâche non privé.")
    }
    let job = try JSONDecoder().decode(Job.self, from: Data(contentsOf: jobURL))
    try run(job)
    try? files.removeItem(at: jobURL)
} catch {
    fputs("KINO update helper: \(error)\n", stderr)
    exit(1)
}
