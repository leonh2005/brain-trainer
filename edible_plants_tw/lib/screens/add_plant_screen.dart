import 'dart:io';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';
import 'package:latlong2/latlong.dart';
import '../models/plant.dart';
import '../services/api_service.dart';

class AddPlantScreen extends StatefulWidget {
  final LatLng? initialLocation;
  const AddPlantScreen({super.key, this.initialLocation});

  @override
  State<AddPlantScreen> createState() => _AddPlantScreenState();
}

class _AddPlantScreenState extends State<AddPlantScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _ediblePartsCtrl = TextEditingController();
  final _notesCtrl = TextEditingController();
  String _category = kPlantCategories[0];
  LatLng? _location;
  final List<File> _photos = [];
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _location = widget.initialLocation;
    if (_location == null) _getGps();
  }

  Future<void> _getGps() async {
    try {
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied ||
          perm == LocationPermission.deniedForever) return;
      final pos = await Geolocator.getCurrentPosition();
      if (mounted) setState(() => _location = LatLng(pos.latitude, pos.longitude));
    } on Exception {
      // 無 GPS 時讓使用者手動輸入
    }
  }

  Future<void> _pickPhoto() async {
    final picker = ImagePicker();
    final xfile = await picker.pickImage(source: ImageSource.camera, imageQuality: 80);
    if (xfile != null) setState(() => _photos.add(File(xfile.path)));
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (_location == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('尚未取得位置，請稍候或重試')),
      );
      return;
    }
    setState(() => _loading = true);
    try {
      final plant = await ApiService.createPlant(Plant(
        name: _nameCtrl.text.trim(),
        category: _category,
        edibleParts: _ediblePartsCtrl.text.trim(),
        notes: _notesCtrl.text.trim(),
        lat: _location!.latitude,
        lng: _location!.longitude,
      ));
      for (final photo in _photos) {
        await ApiService.uploadPhoto(plant.id!, photo);
      }
      if (mounted) Navigator.pop(context, true);
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('新增植物')),
      // 儲存按鈕固定在底部，不隨鍵盤消失
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
          child: FilledButton(
            onPressed: _loading ? null : _submit,
            style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
            child: _loading
                ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Text('儲存'),
          ),
        ),
      ),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextFormField(
              controller: _nameCtrl,
              decoration: const InputDecoration(labelText: '植物名稱 *'),
              textInputAction: TextInputAction.done,
              validator: (v) => (v == null || v.trim().isEmpty) ? '請輸入名稱' : null,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              value: _category,
              decoration: const InputDecoration(labelText: '類別'),
              items: kPlantCategories
                  .map((c) => DropdownMenuItem(value: c, child: Text(c)))
                  .toList(),
              onChanged: (v) => setState(() => _category = v!),
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _ediblePartsCtrl,
              decoration: const InputDecoration(labelText: '可食用部位（葉、果、根…）'),
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _notesCtrl,
              decoration: const InputDecoration(labelText: '備註'),
              maxLines: 3,
            ),
            const SizedBox(height: 16),
            if (_location != null)
              Text(
                '📍 ${_location!.latitude.toStringAsFixed(5)}, ${_location!.longitude.toStringAsFixed(5)}',
                style: const TextStyle(color: Colors.green),
              )
            else
              const Text('📍 正在取得 GPS…', style: TextStyle(color: Colors.orange)),
            const SizedBox(height: 16),
            Row(
              children: [
                ElevatedButton.icon(
                  onPressed: _pickPhoto,
                  icon: const Icon(Icons.camera_alt),
                  label: const Text('拍照'),
                ),
                const SizedBox(width: 8),
                Text('${_photos.length} 張'),
              ],
            ),
            if (_photos.isNotEmpty) ...[
              const SizedBox(height: 8),
              SizedBox(
                height: 80,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: _photos.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 8),
                  itemBuilder: (_, i) => ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: Image.file(_photos[i], width: 80, height: 80, fit: BoxFit.cover),
                  ),
                ),
              ),
            ],
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
}
